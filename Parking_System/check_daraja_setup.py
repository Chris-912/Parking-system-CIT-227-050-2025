"""Pre-flight check for the Daraja (M-Pesa) settings before taking real payments.

Usage:
    python check_daraja_setup.py
    python check_daraja_setup.py --stk 254708374149 1     # also sends a live STK Push prompt

Exit code 0 means every hard requirement passed; 1 means something must be fixed.
The settings themselves are read from Parking_System/.env, exactly like the app does.
"""

import argparse
import ipaddress
import os
import socket
import sys
from urllib.parse import urlparse

import requests

import module4_payment as payment

REQUEST_TIMEOUT = 20
PRODUCTION_BASE_URL = 'https://api.safaricom.co.ke'
SANDBOX_SHORTCODE = '174379'
SANDBOX_TEST_PHONE = '254708374149'
SANDBOX_PASSKEY_PREFIX = 'bfb279f9'
TUNNEL_HOSTS = (
    'ngrok.io', 'ngrok-free.app', 'ngrok.app', 'ngrok-free.dev',
    'loca.lt', 'trycloudflare.com', 'serveo.net', 'localhost.run', 'bore.pub',
)

PASS, WARN, FAIL = 'PASS', 'WARN', 'FAIL'


def _report(level, message):
    """Prints one result line and returns 1 when it counts as a blocker."""
    print(f'  [{level}] {message}')
    return 1 if level == FAIL else 0


def _mask(value):
    value = (value or '').strip()
    if not value:
        return ''
    if len(value) <= 8:
        return '*' * len(value)
    return f'{value[:4]}...{value[-4:]}'


def check_settings():
    """Prints every setting the app reads, masking anything secret."""
    print('1. Settings read from Parking_System/.env')
    shown = (
        ('DARAJA_ENV', False),
        ('DARAJA_CONSUMER_KEY', True),
        ('DARAJA_CONSUMER_SECRET', True),
        ('DARAJA_SHORTCODE', False),
        ('DARAJA_PASSKEY', True),
        ('DARAJA_CALLBACK_URL', False),
        ('DARAJA_TRANSACTION_TYPE', False),
        ('DARAJA_ACCOUNT_REFERENCE', False),
    )
    for name, secret in shown:
        raw = os.getenv(name, '').strip()
        print(f'  {name:<26} = {_mask(raw) if secret else raw or "(not set)"}')

    try:
        return payment._daraja_config()
    except (RuntimeError, ValueError) as error:
        _report(FAIL, str(error))
        return None


def _resolves_to_private(host):
    try:
        address = ipaddress.ip_address(socket.gethostbyname(host))
    except (socket.gaierror, ValueError, OSError):
        return None
    return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved


def check_callback(config):
    print('\n2. Callback URL requirements')
    blockers = 0
    parsed = urlparse(config['callback_url'])
    host = parsed.hostname or ''

    if parsed.scheme != 'https':
        blockers += _report(FAIL, 'Daraja rejects callback URLs that are not HTTPS.')
    if parsed.path != payment.CALLBACK_PATH:
        blockers += _report(WARN, f'Path is {parsed.path}, but the app serves {payment.CALLBACK_PATH}.')
    if _resolves_to_private(host):
        blockers += _report(FAIL, f'{host} resolves to a private or loopback address, so Safaricom cannot reach it.')
    if any(host.endswith(tunnel) for tunnel in TUNNEL_HOSTS):
        blockers += _report(WARN, 'Tunnel host detected. Fine while testing, but free tunnel URLs change on every restart, so production needs a permanent domain.')
    if not blockers:
        _report(PASS, f'Callback URL looks reachable: {config["callback_url"]}')
    return blockers


def check_access_token(config):
    print('\n3. Daraja OAuth (consumer key and secret)')
    url = f"{config['base_url']}/oauth/v1/generate"
    try:
        response = requests.get(
            url,
            params={'grant_type': 'client_credentials'},
            auth=(config['consumer_key'], config['consumer_secret']),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as error:
        _report(FAIL, f'Could not reach {url}: {error}')
        return False

    if response.status_code != 200 or not response.json().get('access_token'):
        _report(FAIL, f'{response.status_code} from {url}: {response.text.strip()[:300]}')
        return False

    _report(PASS, f'Access token issued by {config["base_url"]}.')
    return True


def check_environment(config):
    print('\n4. Environment consistency')
    blockers = 0
    production = config['base_url'] == PRODUCTION_BASE_URL

    if production:
        _report(PASS, 'Production endpoints (api.safaricom.co.ke). Real money will move.')
        if config['shortcode'] == SANDBOX_SHORTCODE:
            blockers += _report(FAIL, f"Shortcode {SANDBOX_SHORTCODE} is Safaricom's shared sandbox shortcode. Production needs your own Paybill or Till number.")
        if config['passkey'].startswith(SANDBOX_PASSKEY_PREFIX):
            blockers += _report(FAIL, 'That passkey is the shared sandbox passkey. Use the passkey Safaricom emailed you for your own shortcode.')
    else:
        _report(WARN, 'Sandbox endpoints (sandbox.safaricom.co.ke). No real money moves.')
        if config['shortcode'] != SANDBOX_SHORTCODE:
            blockers += _report(WARN, f'Sandbox only recognises shortcode {SANDBOX_SHORTCODE}.')
        _report(WARN, f'In sandbox only {SANDBOX_TEST_PHONE} is a recognised test number.')

    if config['transaction_type'] == 'CustomerBuyGoodsOnline':
        _report(WARN, 'CustomerBuyGoodsOnline expects a Till number as the shortcode and ignores AccountReference.')
    return blockers


def send_live_stk(config, phone, amount):
    print(f'\n6. Live STK Push test to {phone} for KES {amount}')
    normalized = payment._normalize_phone(phone)
    if not normalized:
        _report(FAIL, 'Not a valid Kenyan number. Expected the 2547XXXXXXXX format.')
        return False

    try:
        result = payment._daraja_stk_push(config, normalized, int(amount), 'SETUP-CHECK')
    except (requests.RequestException, RuntimeError, ValueError) as error:
        _report(FAIL, f'STK Push rejected: {error}')
        return False

    _report(PASS, f"CheckoutRequestID {result['CheckoutRequestID']}: {result.get('CustomerMessage', '')}")
    print('  Approve the prompt on the phone to finish the check.')
    print(f'  The result arrives at {config["callback_url"]} and is written to Parking_Sessions.')
    return True


def main():
    parser = argparse.ArgumentParser(description='Verify the Daraja settings before taking real payments.')
    parser.add_argument(
        '--stk',
        nargs=2,
        metavar=('PHONE', 'AMOUNT'),
        help='Also send one real STK Push prompt, for example --stk 254712345678 1',
    )
    args = parser.parse_args()

    print('Daraja pre-flight check\n=======================')
    config = check_settings()
    if config is None:
        print('\nResult: FAIL - fix the settings above, then run this check again.')
        return 1

    blockers = check_callback(config)
    if not check_access_token(config):
        print('\nResult: FAIL - the consumer key and secret could not be used to get a token.')
        return 1
    blockers += check_environment(config)

    print('\n5. Live STK Push')
    if args.stk:
        if config['base_url'] == PRODUCTION_BASE_URL:
            _report(WARN, 'This is the production environment: that prompt will move real money.')
    else:
        _report(WARN, 'Skipped. Add --stk PHONE AMOUNT to send one real prompt.')

    if blockers:
        print(f'\nResult: FAIL - {blockers} hard requirement(s) still block going live.')
        return 1

    if args.stk and not send_live_stk(config, args.stk[0], args.stk[1]):
        return 1

    print('\nResult: PASS - configuration is valid.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
