from datetime import datetime

from flask import Blueprint, jsonify

from module5_database import get_db_connection

management_bp = Blueprint('management', __name__)


@management_bp.route('/api/revenue', methods=['GET'])
def get_daily_revenue():
    """Generates a daily revenue report."""
    conn = get_db_connection()
    today = datetime.now().strftime("%Y-%m-%d") + "%"

    total = conn.execute(
        "SELECT SUM(AmountPaid) FROM Payment_Logs WHERE PaymentDate LIKE ?",
        (today,),
    ).fetchone()[0] or 0
    count = conn.execute(
        "SELECT COUNT(*) FROM Payment_Logs WHERE PaymentDate LIKE ?",
        (today,),
    ).fetchone()[0]

    conn.close()
    return jsonify({
        "revenue": total,
        "exits": count,
        "date": datetime.now().strftime("%Y-%m-%d")
    })
