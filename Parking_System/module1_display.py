import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template

from module2_entry import entry_bp
from module3_exit import exit_bp
from module4_payment import payment_bp
from module5_database import get_db_connection, init_db
from module6_management import management_bp

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Runs at import time so a WSGI server such as waitress or gunicorn also prepares the schema.
init_db()

app = Flask(__name__)

app.register_blueprint(entry_bp)
app.register_blueprint(exit_bp)
app.register_blueprint(payment_bp)
app.register_blueprint(management_bp)


@app.route('/')
def index():
    """Displays the number of available parking slots."""
    conn = get_db_connection()
    available = conn.execute("SELECT COUNT(*) FROM Parking_Slots WHERE Status = 'Available'").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM Parking_Slots").fetchone()[0]
    conn.close()
    payment_environment = os.getenv('DARAJA_ENV', 'sandbox').strip().lower() or 'sandbox'
    return render_template(
        'index.html',
        available=available,
        total=total,
        payment_environment=payment_environment,
    )


@app.route('/api/slots')
def get_slots():
    """Returns each parking slot and whether it is available or occupied."""
    conn = get_db_connection()
    slots = conn.execute(
        "SELECT SlotID, Status FROM Parking_Slots ORDER BY SlotID"
    ).fetchall()
    conn.close()
    return {"slots": [{
        "id": slot['SlotID'],
        "label": f"G{slot['SlotID']}",
        "status": slot['Status'],
    } for slot in slots]}


@app.route('/api/active-vehicles')
def get_active_vehicles():
    """Returns the vehicles currently parked and how long they have been inside."""
    conn = get_db_connection()
    vehicles = conn.execute(
        "SELECT LicensePlate, SlotID, EntryTime, Status FROM Parking_Sessions WHERE Status IN ('Active', 'Pending_Payment') ORDER BY EntryTime"
    ).fetchall()
    conn.close()
    return {"vehicles": [{
        "plate": row['LicensePlate'],
        "slot": f"G{row['SlotID']}",
        "entryTime": row['EntryTime'],
        "status": row['Status'],
    } for row in vehicles]}


@app.route('/healthz')
def healthz():
    """Liveness probe; also confirms the public host that Daraja must reach is up."""
    conn = get_db_connection()
    available = conn.execute("SELECT COUNT(*) FROM Parking_Slots WHERE Status = 'Available'").fetchone()[0]
    conn.close()
    return jsonify({
        "status": "ok",
        "environment": os.getenv('DARAJA_ENV', 'sandbox').strip().lower() or 'sandbox',
        "availableSlots": available,
    })


def _flag(name, default):
    """Reads a boolean environment flag, falling back to default when it is unset."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


if __name__ == '__main__':
    production = os.getenv('DARAJA_ENV', 'sandbox').strip().lower() == 'production'
    # The Werkzeug debugger allows remote code execution, so it stays off once real money is involved.
    app.run(
        debug=_flag('FLASK_DEBUG', not production),
        host=os.getenv('FLASK_HOST', '127.0.0.1').strip() or '127.0.0.1',
        port=int(os.getenv('FLASK_PORT', '5000')),
    )

