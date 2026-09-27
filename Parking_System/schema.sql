DROP TABLE IF EXISTS Payment_Logs;
DROP TABLE IF EXISTS Parking_Sessions;
DROP TABLE IF EXISTS Parking_Slots;

CREATE TABLE Parking_Slots (
    SlotID INTEGER PRIMARY KEY AUTOINCREMENT,
    Status TEXT DEFAULT 'Available'
);

CREATE TABLE Parking_Sessions (
    SessionID INTEGER PRIMARY KEY AUTOINCREMENT,
    LicensePlate TEXT NOT NULL,
    SlotID INTEGER,
    EntryTime DATETIME NOT NULL,
    ExitTime DATETIME,
    AmountDue REAL,
    Status TEXT DEFAULT 'Active',
    CheckoutRequestID TEXT,
    MerchantRequestID TEXT,
    PaymentStatus TEXT NOT NULL DEFAULT 'NotStarted',
    PaymentMessage TEXT,
    PaymentPhoneNumber TEXT,
    MpesaReceiptNumber TEXT,
    FOREIGN KEY(SlotID) REFERENCES Parking_Slots(SlotID)
);

CREATE TABLE Payment_Logs (
    PaymentID INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionID INTEGER,
    PhoneNumber TEXT,
    AmountPaid REAL,
    PaymentDate DATETIME,
    MpesaReceiptNumber TEXT,
    FOREIGN KEY(SessionID) REFERENCES Parking_Sessions(SessionID)
);

INSERT INTO Parking_Slots (Status) VALUES 
('Available'),('Available'),('Available'),('Available'),('Available'),
('Available'),('Available'),('Available'),('Available'),('Available'),
('Available'),('Available'),('Available'),('Available'),('Available'),
('Available'),('Available'),('Available'),('Available'),('Available'),
('Available'),('Available'),('Available'),('Available'),('Available'),
('Available'),('Available'),('Available'),('Available'),('Available'),
('Available'),('Available'),('Available'),('Available'),('Available'),
('Available'),('Available'),('Available'),('Available'),('Available'),
('Available'),('Available'),('Available'),('Available'),('Available'),
('Available'),('Available'),('Available'),('Available'),('Available');
