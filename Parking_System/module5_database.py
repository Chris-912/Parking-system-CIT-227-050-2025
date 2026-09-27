import os
import sqlite3

DB_NAME = "modern_parking.db"


def get_db_path():
    return os.path.join(os.path.dirname(__file__), DB_NAME)


def get_db_connection():
    """Establishes and returns a database connection."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Creates the database schema and applies safe upgrades to existing databases."""
    db_path = get_db_path()
    if not os.path.exists(db_path):
        conn = get_db_connection()
        schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        with open(schema_path, 'r', encoding='utf-8') as file:
            conn.executescript(file.read())
        conn.commit()
        print("Dynamic Database Initialized.")

    conn = get_db_connection()
    session_columns = {row['name'] for row in conn.execute('PRAGMA table_info(Parking_Sessions)')}
    for name, declaration in {
        'CheckoutRequestID': 'TEXT',
        'MerchantRequestID': 'TEXT',
        'PaymentStatus': "TEXT NOT NULL DEFAULT 'NotStarted'",
        'PaymentMessage': 'TEXT',
        'PaymentPhoneNumber': 'TEXT',
        'MpesaReceiptNumber': 'TEXT',
        'PaymentUpdatedAt': 'TEXT',
    }.items():
        if name not in session_columns:
            conn.execute(f'ALTER TABLE Parking_Sessions ADD COLUMN {name} {declaration}')

    payment_columns = {row['name'] for row in conn.execute('PRAGMA table_info(Payment_Logs)')}
    if 'MpesaReceiptNumber' not in payment_columns:
        conn.execute('ALTER TABLE Payment_Logs ADD COLUMN MpesaReceiptNumber TEXT')
    conn.commit()
    conn.close()
