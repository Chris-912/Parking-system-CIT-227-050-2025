import math
from datetime import datetime

from flask import Blueprint, jsonify, request

from module5_database import get_db_connection

exit_bp = Blueprint('exit', __name__)


@exit_bp.route('/api/exit', methods=['POST'])
def vehicle_exit():
    """Calculates the parking duration and fee when a vehicle exits."""
    payload = request.get_json(silent=True) or {}
    plate = (payload.get('plate') or '').strip().upper()
    if not plate:
        return jsonify({"error": "License plate is required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    session = cursor.execute(
        "SELECT * FROM Parking_Sessions WHERE UPPER(TRIM(LicensePlate)) = ? AND Status IN ('Active', 'Pending_Payment') ORDER BY SessionID DESC LIMIT 1",
        (plate,),
    ).fetchone()
    if not session:
        conn.close()
        return jsonify({"error": "Vehicle not found"}), 404

    entry_time = datetime.strptime(session['EntryTime'], "%Y-%m-%d %H:%M:%S")
    is_pending = session['Status'] == 'Pending_Payment'
    exit_time = (
        datetime.strptime(session['ExitTime'], "%Y-%m-%d %H:%M:%S")
        if is_pending and session['ExitTime']
        else datetime.now()
    )
    minutes_spent = math.ceil((exit_time - entry_time).total_seconds() / 60)

    if is_pending:
        fee = int(round(float(session['AmountDue'] or 0)))
    else:
        fee = 0
        if minutes_spent <= 30:
            fee = 0
        elif minutes_spent <= 120:
            fee = 50
        elif minutes_spent <= 240:
            fee = 100
        elif minutes_spent <= 360:
            fee = 300
        else:
            fee = 500

    if not is_pending:
        cursor.execute(
            "UPDATE Parking_Sessions SET ExitTime = ?, AmountDue = ?, Status = 'Pending_Payment' WHERE SessionID = ?",
            (exit_time.strftime("%Y-%m-%d %H:%M:%S"), fee, session['SessionID']),
        )
        conn.commit()
    conn.close()

    return jsonify({
        "plate": plate,
        "slot": f"G{session['SlotID']}",
        "sessionId": session['SessionID'],
        "time": minutes_spent,
        "fee": fee,
        "paymentStatus": session['PaymentStatus'] or 'NotStarted',
        "message": "Proceed to payment."
    })
