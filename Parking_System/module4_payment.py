import base64
import math
import os
import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

from module5_database import get_db_connection

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

payment_bp = Blueprint('payment', __name__)
REQUEST_TIMEOUT = 20
CALLBACK_PATH = '/api/pay/mpesa/callback'
# A prompt gets this long to finish before we ask Daraja directly, in case the callback was lost.
QUERY_AFTER_SECONDS = 20
# Never ask Daraja about the same prompt more often than this.
QUERY_THROTTLE_SECONDS = 10
# M-Pesa holds the subscriber lock briefly after a prompt, so an instant retry collides with 1001.
RETRY_COOLDOWN_SECONDS = 20
SANDBOX_TEST_PHONE = '254708374149'
SANDBOX_TEST_PHONE_LOCAL = '0708374149'
# A Daraja app shows placeholders such as "N/A" when no M-Pesa product is assigned yet.
UNSET_PLACEHOLDERS = {'n/a', 'na', 'none', 'null', 'nil', '-', 'tbd', 'pending'}
# Tracks when we last queried Daraja for a checkout, keyed by CheckoutRequestID.
_LAST_QUERY = {}


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _setting(name, default=''):
    """Reads an environment setting, treating portal placeholders such as N/A as unset."""
    value = os.getenv(name, default).strip()
    return '' if value.lower() in UNSET_PLACEHOLDERS else value


def _daraja_config():
    mode = _setting('DARAJA_ENV', 'sandbox').lower() or 'sandbox'
    if mode not in {'sandbox', 'production'}:
        raise ValueError('DARAJA_ENV must be sandbox or production')

    callback_url = _setting('DARAJA_CALLBACK_URL')
    if callback_url and not urlparse(callback_url).path.strip('/'):
        callback_url = f'{callback_url.rstrip("/")}{CALLBACK_PATH}'

    config = {
        'consumer_key': _setting('DARAJA_CONSUMER_KEY'),
        'consumer_secret': _setting('DARAJA_CONSUMER_SECRET'),
        'shortcode': _setting('DARAJA_SHORTCODE'),
        'passkey': _setting('DARAJA_PASSKEY'),
        'callback_url': callback_url,
        'transaction_type': _setting('DARAJA_TRANSACTION_TYPE', 'CustomerPayBillOnline') or 'CustomerPayBillOnline',
        'account_reference': (_setting('DARAJA_ACCOUNT_REFERENCE', 'Parking') or 'Parking')[:12],
        'base_url': 'https://sandbox.safaricom.co.ke' if mode == 'sandbox' else 'https://api.safaricom.co.ke',
    }
    required = ('consumer_key', 'consumer_secret', 'shortcode', 'passkey', 'callback_url')
    missing = [f'DARAJA_{key.upper()}' for key in required if not config[key]]
    if missing:
        raise RuntimeError(
            f"Daraja is not configured. Add {', '.join(missing)} to Parking_System/.env (see .env.example)."
        )
    if config['transaction_type'] not in {'CustomerPayBillOnline', 'CustomerBuyGoodsOnline'}:
        raise ValueError('DARAJA_TRANSACTION_TYPE must be CustomerPayBillOnline or CustomerBuyGoodsOnline')
    if not config['callback_url'].startswith('https://'):
        raise ValueError('DARAJA_CALLBACK_URL must be a public HTTPS URL')
    return config


def _normalize_phone(value):
    digits = re.sub(r'\D', '', str(value or ''))
    if digits.startswith('0'):
        digits = '254' + digits[1:]
    elif digits.startswith('7') or digits.startswith('1'):
        digits = '254' + digits
    if not re.fullmatch(r'254[17]\d{8}', digits):
        return None
    return digits


def _daraja_access_token(config):
    """Fetches a short-lived OAuth token. Daraja tokens expire after one hour."""
    token_response = requests.get(
        f"{config['base_url']}/oauth/v1/generate",
        params={'grant_type': 'client_credentials'},
        auth=(config['consumer_key'], config['consumer_secret']),
        timeout=REQUEST_TIMEOUT,
    )
    token_response.raise_for_status()
    access_token = token_response.json().get('access_token')
    if not access_token:
        raise RuntimeError('Daraja did not return an access token')
    return access_token


def _stk_password(config, timestamp):
    """Builds the base64 shortcode+passkey+timestamp password Daraja expects."""
    return base64.b64encode(
        f'{config["shortcode"]}{config["passkey"]}{timestamp}'.encode('utf-8')
    ).decode('ascii')


def _daraja_stk_push(config, phone, amount, session_id):
    access_token = _daraja_access_token(config)

    timestamp = datetime.now(ZoneInfo('Africa/Nairobi')).strftime('%Y%m%d%H%M%S')
    encoded_password = _stk_password(config, timestamp)
    response = requests.post(
        f"{config['base_url']}/mpesa/stkpush/v1/processrequest",
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'BusinessShortCode': config['shortcode'],
            'Password': encoded_password,
            'Timestamp': timestamp,
            'TransactionType': config['transaction_type'],
            'Amount': amount,
            'PartyA': phone,
            'PartyB': config['shortcode'],
            'PhoneNumber': phone,
            'CallBackURL': config['callback_url'],
            'AccountReference': config['account_reference'] or f'P{session_id}',
            'TransactionDesc': f'Parking session {session_id}',
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()
    if str(result.get('ResponseCode')) != '0' or not result.get('CheckoutRequestID'):
        raise RuntimeError(result.get('CustomerMessage') or result.get('errorMessage') or 'Daraja rejected the STK Push request')
    return result


def _daraja_stk_query(config, checkout_request_id):
    """Asks Daraja for the terminal result of a prompt, for when the callback never arrives.

    Daraja answers with 500.001.1001 while the prompt is still open, so that means "not finished".
    """
    access_token = _daraja_access_token(config)
    timestamp = datetime.now(ZoneInfo('Africa/Nairobi')).strftime('%Y%m%d%H%M%S')
    response = requests.post(
        f"{config['base_url']}/mpesa/stkpushquery/v1/query",
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'BusinessShortCode': config['shortcode'],
            'Password': _stk_password(config, timestamp),
            'Timestamp': timestamp,
            'CheckoutRequestID': checkout_request_id,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code >= 500:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if str(payload.get('errorCode', '')).startswith('500.001.1001'):
        return None
    return payload


def _apply_payment_outcome(conn, session, result_code, result_desc, received_amount, receipt, phone, amount_confirmed=True):
    """Applies a terminal Daraja result to a session.

    Shared by the callback endpoint and the query fallback so both paths judge the money
    identically. A session is only marked Paid when Daraja reports success, and:

    * the callback path also requires an amount equal to the fee, because the callback is an
      unauthenticated inbound POST that could in principle be forged, while
    * the query path is authenticated with our own OAuth token but returns no amount at all,
      so a success there is trusted as-is and the missing receipt is recorded for follow-up.
    """
    expected_amount = int(round(float(session['AmountDue'] or 0)))
    try:
        result_is_success = int(result_code) == 0
    except (TypeError, ValueError):
        result_is_success = False
    try:
        received_amount = float(received_amount)
    except (TypeError, ValueError):
        received_amount = None

    amount_is_settled = received_amount == expected_amount
    if result_is_success and not amount_is_settled and not amount_confirmed and received_amount is None:
        # Daraja's query endpoint confirmed success but reports no amount; the customer cannot
        # change the amount of a prompt we raised, so this is accepted and flagged for follow-up.
        amount_is_settled = True

    if result_is_success and amount_is_settled:
        now = _now()
        note = 'Payment confirmed.' if receipt else 'Payment confirmed by Daraja. Receipt not returned; check the M-Pesa statement.'
        conn.execute(
            "UPDATE Parking_Sessions SET PaymentStatus = 'Paid', MpesaReceiptNumber = ?, "
            "PaymentMessage = ?, PaymentUpdatedAt = ? WHERE SessionID = ?",
            (receipt, note, now, session['SessionID']),
        )
        conn.execute(
            'INSERT INTO Payment_Logs (SessionID, PhoneNumber, AmountPaid, PaymentDate, MpesaReceiptNumber) VALUES (?, ?, ?, ?, ?)',
            (session['SessionID'], phone or session['PaymentPhoneNumber'], expected_amount, now, receipt),
        )
        return True

    message = result_desc or 'Payment was not completed.'
    if result_is_success and received_amount is not None and received_amount != expected_amount:
        message = 'Payment amount did not match the parking fee. Contact the parking manager.'
    elif result_is_success:
        message = 'Payment amount could not be verified. Contact the parking manager.'
    conn.execute(
        "UPDATE Parking_Sessions SET PaymentStatus = 'Failed', PaymentMessage = ?, PaymentUpdatedAt = ? WHERE SessionID = ?",
        (str(message)[:250], _now(), session['SessionID']),
    )
    return False


def _reconcile_requested_payment(session):
    """Settles a prompt whose callback never arrived, by asking Daraja directly.

    STK callbacks can be lost to a tunnel restart or a network blip, which would otherwise
    leave a session waiting forever. Runs only after the prompt has had time to finish, and
    at most once every QUERY_THROTTLE_SECONDS, so polling cannot hammer Daraja.
    """
    checkout_id = session['CheckoutRequestID']
    if not checkout_id:
        return None

    started_at = session['PaymentUpdatedAt'] if 'PaymentUpdatedAt' in session.keys() else None
    if started_at:
        try:
            if (datetime.now() - datetime.strptime(started_at, '%Y-%m-%d %H:%M:%S')).total_seconds() < QUERY_AFTER_SECONDS:
                return None
        except ValueError:
            pass

    last_query = _LAST_QUERY.get(checkout_id)
    if last_query is not None:
        if (datetime.now() - last_query).total_seconds() < QUERY_THROTTLE_SECONDS:
            return None
    _LAST_QUERY[checkout_id] = datetime.now()
    if len(_LAST_QUERY) > 500:
        _LAST_QUERY.clear()

    try:
        config = _daraja_config()
        result = _daraja_stk_query(config, checkout_id)
    except (requests.RequestException, RuntimeError, ValueError):
        return None
    if not result:
        return None
    if result.get('ResultCode') is None:
        # Not a terminal result, so leave the session exactly as it is.
        return None

    metadata = {
        item.get('Name'): item.get('Value')
        for item in ((result.get('CallbackMetadata') or {}).get('Item') or [])
        if item.get('Name')
    }

    conn = get_db_connection()
    conn.execute('BEGIN IMMEDIATE')
    current = conn.execute(
        "SELECT * FROM Parking_Sessions WHERE SessionID = ? AND PaymentStatus = 'Requested'",
        (session['SessionID'],),
    ).fetchone()
    if not current:
        conn.commit()
        conn.close()
        return None

    _apply_payment_outcome(
        conn, current,
        result.get('ResultCode'),
        result.get('ResultDesc'),
        metadata.get('Amount'),
        str(metadata.get('MpesaReceiptNumber') or '') or None,
        str(metadata.get('PhoneNumber') or '') or None,
        amount_confirmed=False,
    )
    conn.commit()
    conn.close()
    return current['SessionID']


@payment_bp.route('/api/pay/mpesa', methods=['POST'])
def initiate_mpesa_payment():
    """Sends an M-Pesa STK prompt; payment is finalized by the Daraja callback."""
    payload = request.get_json(silent=True) or {}
    plate = (payload.get('plate') or '').strip().upper()
    if not plate:
        return jsonify({'error': 'Plate number is required'}), 400

    conn = get_db_connection()
    session = conn.execute(
        "SELECT * FROM Parking_Sessions WHERE UPPER(TRIM(LicensePlate)) = ? AND Status = 'Pending_Payment' ORDER BY SessionID DESC LIMIT 1",
        (plate,),
    ).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'No pending payment found'}), 404

    fee = int(round(float(session['AmountDue'] or 0)))
    if session['PaymentStatus'] == 'Paid':
        conn.close()
        return jsonify({'status': 'Paid', 'message': 'Payment confirmed. Print the receipt to open the barrier.'})

    if fee <= 0:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            "UPDATE Parking_Sessions SET PaymentStatus = 'Paid', PaymentMessage = 'No payment required.', PaymentUpdatedAt = ? WHERE SessionID = ?",
            (_now(), session['SessionID']),
        )
        conn.execute(
            'INSERT INTO Payment_Logs (SessionID, PhoneNumber, AmountPaid, PaymentDate) VALUES (?, ?, ?, ?)',
            (session['SessionID'], None, 0, now),
        )
        conn.commit()
        conn.close()
        return jsonify({'status': 'Paid', 'message': 'No fee is due. Print the receipt to open the barrier.'})

    phone = _normalize_phone(payload.get('phone'))
    if not phone:
        conn.close()
        return jsonify({'error': 'Enter a valid Kenyan M-Pesa number, such as 0712345678.'}), 400

    payment_status = session['PaymentStatus'] or 'NotStarted'
    if payment_status in {'Initiating', 'Requested'}:
        conn.close()
        return jsonify({'status': payment_status, 'message': 'An M-Pesa prompt is already pending. Check the phone.'}), 200

    # M-Pesa keeps the subscriber locked for a moment after a prompt ends. Retrying instantly
    # returns 1001 and can queue a second prompt, so make the driver wait briefly.
    if payment_status == 'Failed':
        last_change = session['PaymentUpdatedAt'] if 'PaymentUpdatedAt' in session.keys() else None
        if last_change:
            try:
                waiting = RETRY_COOLDOWN_SECONDS - (datetime.now() - datetime.strptime(last_change, '%Y-%m-%d %H:%M:%S')).total_seconds()
            except ValueError:
                waiting = 0
            if waiting > 0:
                conn.close()
                return jsonify({
                    'error': f'Please wait {int(waiting) + 1} seconds before trying again while M-Pesa closes the previous prompt.'
                }), 429

    try:
        config = _daraja_config()
    except (RuntimeError, ValueError) as error:
        conn.close()
        return jsonify({'error': str(error)}), 503

    if config['base_url'] == 'https://sandbox.safaricom.co.ke' and phone != SANDBOX_TEST_PHONE:
        conn.close()
        return jsonify({
            'error': f'Sandbox cannot send prompts to real phones. Use simulator number {SANDBOX_TEST_PHONE_LOCAL}, or configure approved production Daraja credentials.'
        }), 400

    conn.execute('BEGIN IMMEDIATE')
    claimed = conn.execute(
        "UPDATE Parking_Sessions SET PaymentStatus = 'Initiating', PaymentPhoneNumber = ?, PaymentMessage = NULL, PaymentUpdatedAt = ? WHERE SessionID = ? AND PaymentStatus NOT IN ('Initiating', 'Requested')",
        (phone, _now(), session['SessionID']),
    ).rowcount
    conn.commit()
    conn.close()
    if not claimed:
        return jsonify({'status': 'Requested', 'message': 'An M-Pesa prompt is already being started. Check the phone.'}), 200

    try:
        result = _daraja_stk_push(config, phone, fee, session['SessionID'])
    except (requests.RequestException, RuntimeError, ValueError) as error:
        conn = get_db_connection()
        conn.execute(
            "UPDATE Parking_Sessions SET PaymentStatus = 'Failed', PaymentMessage = ?, PaymentUpdatedAt = ? WHERE SessionID = ? AND PaymentStatus = 'Initiating'",
            (str(error)[:250], _now(), session['SessionID']),
        )
        conn.commit()
        conn.close()
        return jsonify({'error': 'Could not start M-Pesa payment. Check Daraja configuration and try again.'}), 502

    conn = get_db_connection()
    conn.execute(
        "UPDATE Parking_Sessions SET PaymentStatus = 'Requested', CheckoutRequestID = ?, MerchantRequestID = ?, PaymentMessage = ?, PaymentUpdatedAt = ? WHERE SessionID = ? AND PaymentStatus = 'Initiating'",
        (result['CheckoutRequestID'], result.get('MerchantRequestID'), result.get('CustomerMessage') or 'M-Pesa prompt sent.', _now(), session['SessionID']),
    )
    conn.commit()
    conn.close()
    return jsonify({
        'status': 'Requested',
        'message': result.get('CustomerMessage') or 'M-Pesa prompt sent. Enter your PIN on your phone to complete payment.',
    })


def _load_session_for_plate(plate):
    """Loads the latest session for a plate together with its most recent payment log."""
    conn = get_db_connection()
    session = conn.execute(
        '''SELECT s.SessionID, s.LicensePlate, s.SlotID, s.EntryTime, s.ExitTime,
                  s.AmountDue, s.Status, s.PaymentStatus, s.PaymentMessage,
                  s.MpesaReceiptNumber, s.CheckoutRequestID, s.PaymentUpdatedAt,
                  p.PhoneNumber, p.AmountPaid, p.PaymentDate
           FROM Parking_Sessions AS s
           LEFT JOIN Payment_Logs AS p ON p.PaymentID = (
               SELECT MAX(p2.PaymentID) FROM Payment_Logs AS p2 WHERE p2.SessionID = s.SessionID
           )
           WHERE UPPER(TRIM(s.LicensePlate)) = ?
           ORDER BY s.SessionID DESC LIMIT 1''',
        (plate,),
    ).fetchone()
    conn.close()
    return session


@payment_bp.route('/api/pay/mpesa/status')
def mpesa_payment_status():
    plate = (request.args.get('plate') or '').strip().upper()
    if not plate:
        return jsonify({'error': 'Plate number is required'}), 400

    session = _load_session_for_plate(plate)
    if not session:
        return jsonify({'error': 'Parking session not found'}), 404

    if session['PaymentStatus'] == 'Requested':
        # A lost callback would leave the driver waiting forever, so confirm with Daraja.
        if _reconcile_requested_payment(session):
            session = _load_session_for_plate(plate)

    status = session['PaymentStatus'] or 'NotStarted'
    if status == 'Paid':
        message = 'Payment confirmed. Print the receipt to open the barrier.'
    elif status == 'Failed':
        message = session['PaymentMessage'] or 'Payment was not completed. Check your phone and try again.'
        lowered = message.lower()
        if 'unreachable' in lowered or 'cannot be reached' in lowered or '1037' in lowered:
            message += ' Make sure the phone is switched on with signal, then try again.'
    else:
        message = session['PaymentMessage'] or 'Waiting for M-Pesa payment confirmation.'

    receipt = None
    if status == 'Paid':
        entered = datetime.strptime(session['EntryTime'], '%Y-%m-%d %H:%M:%S')
        exited = datetime.strptime(session['ExitTime'], '%Y-%m-%d %H:%M:%S') if session['ExitTime'] else datetime.now()
        receipt = {
            'number': session['MpesaReceiptNumber'] or (f"FREE-{session['SessionID']:06d}" if not session['AmountPaid'] else 'CONFIRMED (no receipt)'),
            'plate': session['LicensePlate'],
            'slot': f"G{session['SlotID']}",
            'amount': session['AmountPaid'] if session['AmountPaid'] is not None else session['AmountDue'] or 0,
            'phone': f"****{session['PhoneNumber'][-4:]}" if session['PhoneNumber'] else 'Not charged',
            'entryTime': session['EntryTime'],
            'exitTime': session['PaymentDate'] or session['ExitTime'],
            'minutes': max(0, math.ceil((exited - entered).total_seconds() / 60)),
        }
    return jsonify({'status': status, 'message': message, 'receipt': receipt})


@payment_bp.route('/api/exit/complete', methods=['POST'])
def complete_vehicle_exit():
    """Opens the barrier and frees the bay after the receipt-print step."""
    payload = request.get_json(silent=True) or {}
    plate = (payload.get('plate') or '').strip().upper()
    if not plate:
        return jsonify({'error': 'Plate number is required'}), 400

    conn = get_db_connection()
    conn.execute('BEGIN IMMEDIATE')
    session = conn.execute(
        "SELECT SessionID, SlotID, Status, PaymentStatus FROM Parking_Sessions WHERE UPPER(TRIM(LicensePlate)) = ? AND Status IN ('Pending_Payment', 'Completed') ORDER BY SessionID DESC LIMIT 1",
        (plate,),
    ).fetchone()
    if not session:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'No pending exit was found for this vehicle.'}), 404
    if session['PaymentStatus'] != 'Paid':
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Payment must be confirmed before the barrier can open.'}), 409

    if session['Status'] != 'Completed':
        conn.execute(
            "UPDATE Parking_Sessions SET Status = 'Completed' WHERE SessionID = ?",
            (session['SessionID'],),
        )
        conn.execute("UPDATE Parking_Slots SET Status = 'Available' WHERE SlotID = ?", (session['SlotID'],))
    conn.commit()
    conn.close()
    return jsonify({
        'message': f"Receipt printed. Barrier opened at G{session['SlotID']}; vehicle may exit.",
        'slot': f"G{session['SlotID']}",
    })


@payment_bp.route('/api/pay/mpesa/callback', methods=['POST'])
def mpesa_callback():
    """Records Daraja's result and opens the slot only for a confirmed payment."""
    body = request.get_json(silent=True) or {}
    callback = (body.get('Body') or {}).get('stkCallback') or {}
    checkout_id = callback.get('CheckoutRequestID')
    if not checkout_id:
        return jsonify({'ResultCode': 0, 'ResultDesc': 'Accepted'})

    metadata = {
        item.get('Name'): item.get('Value')
        for item in ((callback.get('CallbackMetadata') or {}).get('Item') or [])
        if item.get('Name')
    }
    result_code = callback.get('ResultCode')
    conn = get_db_connection()
    conn.execute('BEGIN IMMEDIATE')
    session = conn.execute(
        "SELECT * FROM Parking_Sessions WHERE CheckoutRequestID = ? AND PaymentStatus = 'Requested'",
        (checkout_id,),
    ).fetchone()
    if not session:
        conn.commit()
        conn.close()
        return jsonify({'ResultCode': 0, 'ResultDesc': 'Accepted'})

    _apply_payment_outcome(
        conn, session,
        result_code,
        callback.get('ResultDesc'),
        metadata.get('Amount'),
        str(metadata.get('MpesaReceiptNumber') or '') or None,
        str(metadata.get('PhoneNumber') or '') or None,
    )

    conn.commit()
    conn.close()
    return jsonify({'ResultCode': 0, 'ResultDesc': 'Accepted'})
