from datetime import datetime

from flask import Blueprint, jsonify, request

from module5_database import get_db_connection

entry_bp = Blueprint('entry', __name__)


@entry_bp.route('/api/entry', methods=['POST'])
def vehicle_entry():
    """Records vehicles on arrival and assigns a parking slot."""
    payload = request.get_json(silent=True) or {}
    plate = (payload.get('plate') or '').strip().upper()
    if not plate:
        return jsonify({"error": "License plate is required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    active = cursor.execute(
        "SELECT * FROM Parking_Sessions WHERE UPPER(TRIM(LicensePlate)) = ? AND Status IN ('Active', 'Pending_Payment')",
        (plate,),
    ).fetchone()
    if active:
        conn.close()
        return jsonify({"error": "Vehicle already parked"}), 400

    slot = cursor.execute(
        "SELECT SlotID FROM Parking_Slots WHERE Status = 'Available' LIMIT 1"
    ).fetchone()
    if not slot:
        conn.close()
        return jsonify({"error": "Parking is full"}), 400

    slot_id = slot['SlotID']
    entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        "UPDATE Parking_Slots SET Status = 'Occupied' WHERE SlotID = ?",
        (slot_id,),
    )
    cursor.execute(
        "INSERT INTO Parking_Sessions (LicensePlate, SlotID, EntryTime) VALUES (?, ?, ?)",
        (plate, slot_id, entry_time),
    )
    session_id = cursor.lastrowid

    conn.commit()
    conn.close()
    slot_name = f'G{slot_id}'
    return jsonify({
        "message": f"Barrier opened. Proceed to parking bay {slot_name}.",
        "ticket": {
            "number": f"T{session_id:06d}",
            "plate": plate,
            "slot": slot_name,
            "entryTime": entry_time,
        },
    })
