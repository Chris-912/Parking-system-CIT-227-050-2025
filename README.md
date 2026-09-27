# Parking Manager: Daraja Setup

The app uses M-Pesa STK Push. A session is marked paid only after Daraja sends a successful callback; its bay is freed after the receipt-print step completes. Until Daraja is configured, payment requests return a configuration error rather than pretending a payment succeeded.

## Sandbox setup

1. Create a Daraja app in the Safaricom Developer Portal and copy its sandbox consumer key and consumer secret. An app with no product assigned yet reports no passkey and no shortcode of its own, so sandbox testing uses Safaricom's shared test values:

   | Setting | Sandbox value |
   | --- | --- |
   | `DARAJA_SHORTCODE` | `174379` |
   | `DARAJA_PASSKEY` | `bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919` |
   | Test phone number | `254708374149` (enter `0708374149` in this app) |

   That passkey is public and identical for every sandbox app. Production needs your own Paybill or Till number and its passkey, with `DARAJA_ENV=production`.
2. Copy `.env.example` to `.env` and fill in the consumer key and consumer secret. Keep `.env` private; it is excluded by `.gitignore`. Real credentials belong in `.env` only, never in `.env.example`.
3. Set `DARAJA_CALLBACK_URL` to the public HTTPS address for this endpoint:

   `https://your-public-host/api/pay/mpesa/callback`

   A bare host such as `https://your-public-host` is upgraded to the full path automatically. Daraja cannot call `localhost`. For local testing, expose the Flask port through a trusted HTTPS tunnel (for example `ngrok http 5000`) and use that tunnel URL. Keep the tunnel running while testing, and remember that a free ngrok URL changes whenever the tunnel restarts.
4. Install dependencies and start the app from this directory:

   ```powershell
   python -m pip install -r requirements.txt
   python module1_display.py
   ```

5. Enter a vehicle to assign a bay (`G1`-`G50`) and print an entry ticket with the plate, bay, and arrival time. At exit, enter the plate to show the parked duration and fee, then enter the M-Pesa number and approve the STK prompt. After Daraja confirms payment, print the receipt; the final print action opens the simulated barrier and frees the bay.

## Ticket and receipt printing

The browser opens its print dialog for an 80 mm receipt layout. Select the installed thermal or receipt printer there; tickets remain on screen for reprinting. The M-Pesa receipt includes Daraja's receipt number, vehicle plate, `G` bay, duration, amount, masked phone number, and payment time. This project simulates the barrier in the app; operating a physical gate requires connecting a gate controller to the exit-completion endpoint.

`DARAJA_ENV` accepts `sandbox` or `production`. Production requires Safaricom-approved production credentials, shortcode/passkey, and a stable public HTTPS callback. `DARAJA_TRANSACTION_TYPE` should be `CustomerPayBillOnline` for a Paybill or `CustomerBuyGoodsOnline` for a Buy Goods till.

## What to expect in sandbox

Sandbox simulates the payment instead of ringing a real phone, so the last step behaves differently from production:

- Only the test number `254708374149` is recognised. Any other number usually fails with `Invalid PhoneNumber`.
- `DS timeout user cannot be reached` means Daraja accepted the STK Push and then called your callback, but nobody approved the simulated prompt. That is a working setup: the app stores that as a failed payment and shows the retry button.
- No real money moves and no receipt is generated until a `ResultCode` of `0` arrives with a matching amount.

## Going live with real M-Pesa

Sandbox credentials are shared test values and cannot be promoted to production. A real passkey and shortcode exist only against a business shortcode that Safaricom has issued to you, so most of this work is paperwork rather than code.

### What you need first

1. **Your own Paybill or Till number.** Apply through Safaricom Business, not the Daraja portal: `https://org.ke.m-pesa.com/` or email `M-PESABusiness@safaricom.co.ke`. This is the usual blocker and it takes longer than the coding. Roughly KES 1,800 to set up plus a small monthly fee for a Paybill; Tills have no monthly fee but a higher per-transaction rate. Personal Paybill or Till accounts cannot be used for API integration.
2. **Business documents**: business name certificate (sole proprietor) or CR12 (limited company), KRA PIN certificate, director's ID or passport, and a clear logo.
3. **A live website** on HTTPS with a real service description and linked privacy and refund policies. Safaricom reviewers check this.
4. **A permanent public HTTPS callback URL.** Free tunnels are for testing only; Daraja verifies that the URL is publicly reachable and rejected callbacks can disable the app.
5. **Your M-PESA Business Portal username**, so Safaricom can confirm you are authorised to link that shortcode to a developer app.

### Then, in the Daraja portal

1. Open `Go Live` in the sidebar, fill in the organization details, and confirm the OTP sent to your registered contact.
2. Link your Paybill or Till to the production app.
3. Wait for approval: usually 1 to 3 working days, sometimes longer while callback URLs and business details are checked.
4. Your production consumer key and secret appear under My Apps. **The passkey arrives by email** and is unique to your shortcode.

### Switching the app over

Only four values change. The code already targets `api.safaricom.co.ke` once `DARAJA_ENV=production`, and the request and callback shapes are identical, so nothing else needs editing.

```dotenv
DARAJA_ENV=production
DARAJA_CONSUMER_KEY=<production key>
DARAJA_CONSUMER_SECRET=<production secret>
DARAJA_SHORTCODE=<your Paybill or Till number>
DARAJA_PASSKEY=<the passkey from the email>
DARAJA_CALLBACK_URL=https://<your-domain>/api/pay/mpesa/callback
```

Use `DARAJA_TRANSACTION_TYPE=CustomerPayBillOnline` with a Paybill, or `CustomerBuyGoodsOnline` with a Till. Rotate the consumer secret that was shared in plain text during setup.

Confirm the settings before trusting them with money:

```powershell
python check_daraja_setup.py                      # settings, callback URL, and OAuth
python check_daraja_setup.py --stk <your phone> 1 # sends one real prompt
```

The check refuses to pass while sandbox values are pointed at production endpoints. The first live prompt settles to your own phone, so you see the prompt, the PIN, the SMS receipt, and the matching row in `Payment_Logs`.

### Deploying for real payments

- Run behind a production WSGI server instead of the Flask development server, for example `python -m pip install waitress` then `waitress-serve --listen=127.0.0.1:8000 module1_display:app`, and terminate TLS at a reverse proxy. `init_db()` now runs on import, so a WSGI server prepares the schema too.
- The Werkzeug debugger switches itself off when `DARAJA_ENV=production`, because it allows remote code execution. Set `FLASK_DEBUG=1` only on a local machine. `FLASK_HOST` and `FLASK_PORT` are configurable.
- Keep the callback returning `{"ResultCode": 0, "ResultDesc": "Accepted"}` for every outcome, including failures. A callback that returns 500 looks unreachable to Daraja.
- `GET /healthz` reports the active environment and available slots, which is a quick way to confirm the public host is up.

### If you cannot get a shortcode

A Paybill or Till needs a registered business and Safaricom approval. If the parking system is for a school, office, or landlord that **already has a Paybill**, you can register that shortcode to a production Daraja app instead. Otherwise, payment aggregators such as IntaSend, Paystack, Flutterwave, Pesapal, and MpesaFlow hold their own shortcodes and settle to your bank or M-Pesa account; they need KYC but not your own Paybill. That route means replacing the Daraja calls in `module4_payment.py` with the aggregator's API while every other module and the callback-driven design stays the same.

## Troubleshooting

- `Daraja is not configured. Add DARAJA_...` - the listed values are missing from `Parking_System/.env`. Placeholders such as `N/A`, `none`, or `-` are treated as missing.
- `Could not start M-Pesa payment` - the app reaches Daraja but the request was rejected. Check that `DARAJA_SHORTCODE` and `DARAJA_PASSKEY` belong to the same environment, and set `DARAJA_ENV=production` for live credentials.
- Payment stays on `Waiting for M-Pesa payment confirmation` - the callback never arrived. Most often the tunnel is down or `DARAJA_CALLBACK_URL` is stale, because a free ngrok URL changes on every tunnel restart. Restart the tunnel, update `.env`, and restart the app.
- Payment amount did not match the parking fee - the amount sent to Daraja and the amount charged must match exactly. This check stops a tampered callback from freeing a slot.
- `Error occurred while sending push request` (`ResultCode 9999`), or `403 Forbidden` with an `Incapsula incident ID` - Safaricom puts an Imperva/Incapsula WAF in front of the sandbox and throttles an IP that sends requests too quickly. Wait a few minutes, then retry once instead of looping; hammering it extends the block. Space repeated tests out, and never build an aggressive retry loop around STK Push.
- A callback for an unknown or already settled `CheckoutRequestID` is acknowledged with `{"ResultCode": 0}` and ignored, so Safaricom's retries cannot double-charge or free a slot twice.

The app uses the system's SQLite database and safely adds Daraja tracking columns when it starts. The Flask development server is for local testing only; deploy behind a production WSGI server and HTTPS for real payments.
