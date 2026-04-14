"""
Realistic Pakistani test data for all mock payment networks.
Phone numbers follow real Pakistani carrier prefixes:
  Jazz:     0300-0309, 0320-0329
  Zong:     0310-0319
  Telenor:  0340-0349
  Ufone:    0330-0339
"""


def normalize_pk_phone(number: str) -> str:
    """Normalize Pakistani phone to +92 format.

    Accepts: 03001001001, 923001001001, +923001001001
    Returns: +923001001001
    """
    number = number.strip().replace("-", "").replace(" ", "")
    if number.startswith("0"):
        return "+92" + number[1:]
    if number.startswith("92") and not number.startswith("+"):
        return "+" + number
    return number


# ── JAZZCASH TEST USERS ──────────────────────────────────────────────────────
JAZZCASH_USERS = {
    "+923001001001": {
        "name": "Ali Hassan",
        "cnic": "35202-1234567-1",
        "balance": 15000.00,
        "daily_limit": 25000.00,
        "daily_sent": 0.00,
        "status": "active",
        "pin_hash": "1234",
        "account_level": "L1",
    },
    "+923001002002": {
        "name": "Sara Khan",
        "cnic": "35202-2345678-2",
        "balance": 8500.00,
        "daily_limit": 25000.00,
        "daily_sent": 5000.00,
        "status": "active",
        "pin_hash": "1234",
        "account_level": "L2",
    },
    "+923001003003": {
        "name": "Omar Farooq",
        "cnic": "35202-3456789-3",
        "balance": 50000.00,
        "daily_limit": 100000.00,
        "daily_sent": 0.00,
        "status": "active",
        "pin_hash": "1234",
        "account_level": "L3",
    },
    "+923201004004": {
        "name": "Fatima Malik",
        "cnic": "35202-4567890-4",
        "balance": 2500.00,
        "daily_limit": 25000.00,
        "daily_sent": 0.00,
        "status": "active",
        "pin_hash": "1234",
        "account_level": "L1",
    },
    "+923001005005": {
        "name": "Usman Tariq",
        "cnic": "35202-5678901-5",
        "balance": 0.00,
        "daily_limit": 25000.00,
        "daily_sent": 0.00,
        "status": "blocked",
        "pin_hash": "1234",
        "account_level": "L1",
    },
}

# ── EASYPAISA TEST USERS ─────────────────────────────────────────────────────
EASYPAISA_USERS = {
    "+923111001001": {
        "name": "Bilal Ahmed",
        "cnic": "42301-1111111-1",
        "balance": 22000.00,
        "daily_limit": 50000.00,
        "daily_sent": 0.00,
        "status": "active",
        "account_tier": "silver",
    },
    "+923111002002": {
        "name": "Ayesha Siddiqui",
        "cnic": "42301-2222222-2",
        "balance": 7800.00,
        "daily_limit": 25000.00,
        "daily_sent": 1200.00,
        "status": "active",
        "account_tier": "basic",
    },
    "+923331003003": {
        "name": "Hamza Rehman",
        "cnic": "42301-3333333-3",
        "balance": 95000.00,
        "daily_limit": 200000.00,
        "daily_sent": 0.00,
        "status": "active",
        "account_tier": "gold",
    },
    "+923111004004": {
        "name": "Zainab Hussain",
        "cnic": "42301-4444444-4",
        "balance": 3200.00,
        "daily_limit": 25000.00,
        "daily_sent": 0.00,
        "status": "active",
        "account_tier": "basic",
    },
}

# ── NAYAPAY TEST USERS ───────────────────────────────────────────────────────
NAYAPAY_USERS = {
    "+923401001001": {
        "name": "Hira Baig",
        "cnic": "61101-1001001-1",
        "balance": 45000.00,
        "daily_limit": 100000.00,
        "daily_sent": 0.00,
        "status": "active",
        "kyc_level": "full",
    },
    "+923441002002": {
        "name": "Saad Qureshi",
        "cnic": "61101-2002002-2",
        "balance": 12000.00,
        "daily_limit": 50000.00,
        "daily_sent": 3000.00,
        "status": "active",
        "kyc_level": "basic",
    },
    "+923451003003": {
        "name": "Nimra Sheikh",
        "cnic": "61101-3003003-3",
        "balance": 180000.00,
        "daily_limit": 500000.00,
        "daily_sent": 0.00,
        "status": "active",
        "kyc_level": "full",
    },
}

# ── UPAY TEST USERS ──────────────────────────────────────────────────────────
UPAY_USERS = {
    "+923051001001": {
        "name": "Kashif Mehmood",
        "cnic": "34101-1001001-1",
        "balance": 9500.00,
        "daily_limit": 25000.00,
        "daily_sent": 0.00,
        "status": "active",
    },
    "+923051002002": {
        "name": "Sana Javed",
        "cnic": "34101-2002002-2",
        "balance": 31000.00,
        "daily_limit": 50000.00,
        "daily_sent": 0.00,
        "status": "active",
    },
    "+923061003003": {
        "name": "Imran Butt",
        "cnic": "34101-3003003-3",
        "balance": 500.00,
        "daily_limit": 10000.00,
        "daily_sent": 0.00,
        "status": "active",
    },
}

# ── SADAPAY TEST USERS ───────────────────────────────────────────────────────
SADAPAY_USERS = {
    "+923211001001": {
        "name": "Amna Tariq",
        "cnic": "35101-1001001-1",
        "balance": 67000.00,
        "daily_limit": 200000.00,
        "daily_sent": 0.00,
        "status": "active",
        "visa_card": "4276-****-****-1001",
    },
    "+923211002002": {
        "name": "Raza Ali",
        "cnic": "35101-2002002-2",
        "balance": 28000.00,
        "daily_limit": 100000.00,
        "daily_sent": 5000.00,
        "status": "active",
        "visa_card": "4276-****-****-1002",
    },
    "+923221003003": {
        "name": "Maria Noor",
        "cnic": "35101-3003003-3",
        "balance": 4200.00,
        "daily_limit": 50000.00,
        "daily_sent": 0.00,
        "status": "active",
        "visa_card": "4276-****-****-1003",
    },
}

# ── BANK ACCOUNTS (for 1LINK IBFT) ──────────────────────────────────────────
BANK_ACCOUNTS = {
    # HBL
    "HBL-0001-1234567890": {
        "bank_code": "HBL",
        "bank_name": "Habib Bank Limited",
        "account_number": "1234567890",
        "account_title": "Muhammad Ali Khan",
        "balance": 250000.00,
        "status": "active",
        "branch_code": "0001",
    },
    "HBL-0001-0987654321": {
        "bank_code": "HBL",
        "bank_name": "Habib Bank Limited",
        "account_number": "0987654321",
        "account_title": "Sara Bibi",
        "balance": 80000.00,
        "status": "active",
        "branch_code": "0001",
    },
    # MCB
    "MCB-0010-1122334455": {
        "bank_code": "MCB",
        "bank_name": "MCB Bank Limited",
        "account_number": "1122334455",
        "account_title": "Omar Riaz",
        "balance": 125000.00,
        "status": "active",
        "branch_code": "0010",
    },
    # UBL
    "UBL-0020-2233445566": {
        "bank_code": "UBL",
        "bank_name": "United Bank Limited",
        "account_number": "2233445566",
        "account_title": "Fatima Zahra",
        "balance": 45000.00,
        "status": "active",
        "branch_code": "0020",
    },
    # Meezan
    "MEZN-0030-3344556677": {
        "bank_code": "MEZN",
        "bank_name": "Meezan Bank Limited",
        "account_number": "3344556677",
        "account_title": "Hassan Bukhari",
        "balance": 320000.00,
        "status": "active",
        "branch_code": "0030",
    },
    # Bank Alfalah
    "ALFH-0040-4455667788": {
        "bank_code": "ALFH",
        "bank_name": "Bank Alfalah Limited",
        "account_number": "4455667788",
        "account_title": "Amina Siddiq",
        "balance": 60000.00,
        "status": "active",
        "branch_code": "0040",
    },
}

# ── BILL DATABASE (for BPSP) ─────────────────────────────────────────────────

# Electricity — LESCO
LESCO_BILLS = {
    "01-23-4567-001": {
        "consumer_name": "Muhammad Ali",
        "address": "House 12, Street 4, Gulberg III, Lahore",
        "amount_due": 3450.00,
        "units_consumed": 320,
        "bill_month": "March 2026",
        "due_date": "2026-04-20",
        "status": "unpaid",
        "surcharge": 0.00,
    },
    "01-23-4567-002": {
        "consumer_name": "Sara Khan",
        "address": "Flat 5B, DHA Phase 6, Lahore",
        "amount_due": 8720.00,
        "units_consumed": 780,
        "bill_month": "March 2026",
        "due_date": "2026-04-20",
        "status": "unpaid",
        "surcharge": 0.00,
    },
    "01-23-4567-003": {
        "consumer_name": "Omar Farooq",
        "address": "Plot 33, Model Town, Lahore",
        "amount_due": 1200.00,
        "units_consumed": 120,
        "bill_month": "March 2026",
        "due_date": "2026-04-15",
        "status": "overdue",
        "surcharge": 150.00,
    },
}

# Electricity — MEPCO
MEPCO_BILLS = {
    "02-10-1234-001": {
        "consumer_name": "Asif Iqbal",
        "address": "Village Chak 45, Multan",
        "amount_due": 2100.00,
        "units_consumed": 210,
        "bill_month": "March 2026",
        "due_date": "2026-04-25",
        "status": "unpaid",
        "surcharge": 0.00,
    },
    "02-10-1234-002": {
        "consumer_name": "Rukhsana Bibi",
        "address": "Ward 7, Dera Ghazi Khan",
        "amount_due": 5600.00,
        "units_consumed": 520,
        "bill_month": "March 2026",
        "due_date": "2026-04-25",
        "status": "unpaid",
        "surcharge": 0.00,
    },
}

# Gas — SNGPL
SNGPL_BILLS = {
    "SNG-1001-2345": {
        "consumer_name": "Muhammad Ali",
        "address": "House 12, Street 4, Gulberg III, Lahore",
        "amount_due": 1850.00,
        "units_consumed": 12,
        "bill_month": "March 2026",
        "due_date": "2026-04-18",
        "status": "unpaid",
        "surcharge": 0.00,
    },
    "SNG-1001-3456": {
        "consumer_name": "Hamza Shah",
        "address": "Johar Town, Lahore",
        "amount_due": 4200.00,
        "units_consumed": 28,
        "bill_month": "March 2026",
        "due_date": "2026-04-18",
        "status": "unpaid",
        "surcharge": 0.00,
    },
}

# Gas — SSGC
SSGC_BILLS = {
    "SSG-2001-1234": {
        "consumer_name": "Kareem Bux",
        "address": "Block 5, Gulshan-e-Iqbal, Karachi",
        "amount_due": 2300.00,
        "units_consumed": 15,
        "bill_month": "March 2026",
        "due_date": "2026-04-22",
        "status": "unpaid",
        "surcharge": 0.00,
    },
}

# Internet — PTCL
PTCL_BILLS = {
    "PTCL-0310-123456": {
        "consumer_name": "Ali Hassan",
        "package": "25 Mbps Unlimited",
        "amount_due": 3500.00,
        "bill_month": "March 2026",
        "due_date": "2026-04-15",
        "status": "unpaid",
        "surcharge": 0.00,
    },
    "PTCL-0310-654321": {
        "consumer_name": "Sara Malik",
        "package": "50 Mbps Business",
        "amount_due": 6500.00,
        "bill_month": "March 2026",
        "due_date": "2026-04-15",
        "status": "overdue",
        "surcharge": 500.00,
    },
}

# Government — Tax / PSID payments
PSID_RECORDS = {
    "PSI-2026-001234": {
        "challan_type": "Income Tax",
        "taxpayer_name": "Muhammad Ali Khan",
        "ntn": "1234567-8",
        "amount_due": 15000.00,
        "tax_year": "2026",
        "due_date": "2026-06-30",
        "status": "unpaid",
        "consumer_name": "Muhammad Ali Khan",  # alias for uniform response
    },
    "PSI-2026-005678": {
        "challan_type": "Property Tax",
        "taxpayer_name": "Omar Farooq",
        "cnic": "35202-3456789-3",
        "amount_due": 8500.00,
        "tax_year": "2026",
        "due_date": "2026-03-31",
        "status": "overdue",
        "consumer_name": "Omar Farooq",
        "surcharge": 0.00,
    },
}

# ── MORE ELECTRICITY COMPANIES ───────────────────────────────────────────────

IESCO_BILLS = {
    "IE-0101-7890001": {
        "consumer_name": "Tariq Mehmood",
        "address": "Street 22, G-9/1, Islamabad",
        "amount_due": 4100.00, "units_consumed": 380,
        "bill_month": "March 2026", "due_date": "2026-04-22",
        "status": "unpaid", "surcharge": 0.00,
    },
    "IE-0101-7890002": {
        "consumer_name": "Naila Anwar",
        "address": "F-7/2, Islamabad",
        "amount_due": 9800.00, "units_consumed": 870,
        "bill_month": "March 2026", "due_date": "2026-04-22",
        "status": "unpaid", "surcharge": 0.00,
    },
}

PESCO_BILLS = {
    "PE-0201-4560001": {
        "consumer_name": "Gul Nawaz",
        "address": "Hayatabad Phase 3, Peshawar",
        "amount_due": 2750.00, "units_consumed": 265,
        "bill_month": "March 2026", "due_date": "2026-04-20",
        "status": "unpaid", "surcharge": 0.00,
    },
    "PE-0201-4560002": {
        "consumer_name": "Shabana Gul",
        "address": "University Town, Peshawar",
        "amount_due": 1500.00, "units_consumed": 140,
        "bill_month": "February 2026", "due_date": "2026-03-25",
        "status": "overdue", "surcharge": 200.00,
    },
}

HESCO_BILLS = {
    "HE-0301-3210001": {
        "consumer_name": "Sikander Memon",
        "address": "Latifabad Unit 8, Hyderabad",
        "amount_due": 3200.00, "units_consumed": 305,
        "bill_month": "March 2026", "due_date": "2026-04-25",
        "status": "unpaid", "surcharge": 0.00,
    },
}

QESCO_BILLS = {
    "QE-0401-2340001": {
        "consumer_name": "Abdul Wahid",
        "address": "Jinnah Town, Quetta",
        "amount_due": 1850.00, "units_consumed": 175,
        "bill_month": "March 2026", "due_date": "2026-04-28",
        "status": "unpaid", "surcharge": 0.00,
    },
}

GEPCO_BILLS = {
    "GE-0501-5670001": {
        "consumer_name": "Umar Draz",
        "address": "Model Town, Gujranwala",
        "amount_due": 4600.00, "units_consumed": 430,
        "bill_month": "March 2026", "due_date": "2026-04-20",
        "status": "unpaid", "surcharge": 0.00,
    },
}

SEPCO_BILLS = {
    "SE-0601-8900001": {
        "consumer_name": "Ramzan Bhutto",
        "address": "Civil Lines, Sukkur",
        "amount_due": 2100.00, "units_consumed": 200,
        "bill_month": "March 2026", "due_date": "2026-04-22",
        "status": "unpaid", "surcharge": 0.00,
    },
}

# ── WATER BILLS ───────────────────────────────────────────────────────────────

KWSB_BILLS = {   # Karachi Water & Sewerage Board
    "KW-7001-0010001": {
        "consumer_name": "Kareem Bux",
        "address": "Block 7, PECHS, Karachi",
        "amount_due": 850.00, "units_consumed": None,
        "bill_month": "March 2026", "due_date": "2026-04-30",
        "status": "unpaid", "surcharge": 0.00,
        "connection_type": "residential",
    },
    "KW-7001-0010002": {
        "consumer_name": "Sadiq Enterprises",
        "address": "Site Area, Karachi",
        "amount_due": 4500.00, "units_consumed": None,
        "bill_month": "March 2026", "due_date": "2026-04-30",
        "status": "unpaid", "surcharge": 0.00,
        "connection_type": "commercial",
    },
    "KW-7001-0010003": {
        "consumer_name": "Nazia Hashmi",
        "address": "Gulshan-e-Iqbal Block 13A, Karachi",
        "amount_due": 650.00, "units_consumed": None,
        "bill_month": "February 2026", "due_date": "2026-03-31",
        "status": "overdue", "surcharge": 100.00,
        "connection_type": "residential",
    },
}

WASA_LHR_BILLS = {   # WASA Lahore
    "WL-8001-1110001": {
        "consumer_name": "Muhammad Ali",
        "address": "Johar Town, Lahore",
        "amount_due": 720.00, "units_consumed": None,
        "bill_month": "March 2026", "due_date": "2026-04-25",
        "status": "unpaid", "surcharge": 0.00,
        "connection_type": "residential",
    },
    "WL-8001-1110002": {
        "consumer_name": "Green Pharmacy",
        "address": "Township Sector A2, Lahore",
        "amount_due": 1800.00, "units_consumed": None,
        "bill_month": "March 2026", "due_date": "2026-04-25",
        "status": "unpaid", "surcharge": 0.00,
        "connection_type": "commercial",
    },
}

WASA_RWP_BILLS = {   # WASA Rawalpindi
    "WR-9001-2220001": {
        "consumer_name": "Asim Shah",
        "address": "Satellite Town Block D, Rawalpindi",
        "amount_due": 680.00, "units_consumed": None,
        "bill_month": "March 2026", "due_date": "2026-04-28",
        "status": "unpaid", "surcharge": 0.00,
        "connection_type": "residential",
    },
}

WASA_FSD_BILLS = {   # WASA Faisalabad
    "WF-9101-3330001": {
        "consumer_name": "Khalid Textile",
        "address": "D-Ground, Faisalabad",
        "amount_due": 2300.00, "units_consumed": None,
        "bill_month": "March 2026", "due_date": "2026-04-22",
        "status": "unpaid", "surcharge": 0.00,
        "connection_type": "commercial",
    },
}

# ── INTERNET BILLS ────────────────────────────────────────────────────────────

STORMFIBER_BILLS = {
    "SF-LHR-100001": {
        "consumer_name": "Ali Hassan",
        "address": "DHA Phase 5, Lahore",
        "package": "100 Mbps Unlimited",
        "amount_due": 2499.00,
        "bill_month": "March 2026", "due_date": "2026-04-10",
        "status": "unpaid", "surcharge": 0.00,
    },
    "SF-KHI-200001": {
        "consumer_name": "Hina Zafar",
        "address": "Clifton Block 4, Karachi",
        "package": "50 Mbps Standard",
        "amount_due": 1799.00,
        "bill_month": "March 2026", "due_date": "2026-04-10",
        "status": "overdue", "surcharge": 250.00,
    },
}

NAYATEL_BILLS = {
    "NT-ISB-300001": {
        "consumer_name": "Tariq Mehmood",
        "address": "F-11 Markaz, Islamabad",
        "package": "200 Mbps Fiber",
        "amount_due": 3500.00,
        "bill_month": "March 2026", "due_date": "2026-04-15",
        "status": "unpaid", "surcharge": 0.00,
    },
}

TRANSWORLD_BILLS = {
    "TW-KHI-400001": {
        "consumer_name": "Alpha Solutions Pvt Ltd",
        "address": "SITE, Karachi",
        "package": "Business 500 Mbps",
        "amount_due": 12000.00,
        "bill_month": "March 2026", "due_date": "2026-04-20",
        "status": "unpaid", "surcharge": 0.00,
    },
}

CYBERNET_BILLS = {
    "CN-LHR-500001": {
        "consumer_name": "Omar Trading Co",
        "address": "MM Alam Road, Gulberg, Lahore",
        "package": "Business 100 Mbps",
        "amount_due": 7500.00,
        "bill_month": "March 2026", "due_date": "2026-04-18",
        "status": "unpaid", "surcharge": 0.00,
    },
}

# ── PHONE BILLS (Postpaid) ────────────────────────────────────────────────────

JAZZ_BILLS = {
    "JZ-POST-03001001": {
        "consumer_name": "Ali Hassan",
        "mobile_number": "03001001001",
        "package": "Jazz Postpaid 1000",
        "amount_due": 1199.00,
        "bill_month": "March 2026", "due_date": "2026-04-15",
        "status": "unpaid", "surcharge": 0.00,
        "data_used_gb": 18.4, "minutes_used": 650,
    },
    "JZ-POST-03201002": {
        "consumer_name": "Fatima Malik",
        "mobile_number": "03201002002",
        "package": "Jazz Postpaid 500",
        "amount_due": 649.00,
        "bill_month": "March 2026", "due_date": "2026-04-15",
        "status": "unpaid", "surcharge": 0.00,
        "data_used_gb": 6.2, "minutes_used": 210,
    },
}

ZONG_BILLS = {
    "ZG-POST-03101001": {
        "consumer_name": "Sara Khan",
        "mobile_number": "03101001001",
        "package": "Zong Postpaid Unlimited",
        "amount_due": 1500.00,
        "bill_month": "March 2026", "due_date": "2026-04-20",
        "status": "unpaid", "surcharge": 0.00,
        "data_used_gb": 32.1, "minutes_used": 900,
    },
}

UFONE_BILLS = {
    "UF-POST-03301001": {
        "consumer_name": "Hamza Rehman",
        "mobile_number": "03301001001",
        "package": "Ufone Postpaid Classic",
        "amount_due": 899.00,
        "bill_month": "March 2026", "due_date": "2026-04-18",
        "status": "overdue", "surcharge": 150.00,
        "data_used_gb": 9.8, "minutes_used": 320,
    },
}

TELENOR_BILLS = {
    "TL-POST-03401001": {
        "consumer_name": "Hira Baig",
        "mobile_number": "03401001001",
        "package": "Telenor Postpaid Max",
        "amount_due": 1299.00,
        "bill_month": "March 2026", "due_date": "2026-04-22",
        "status": "unpaid", "surcharge": 0.00,
        "data_used_gb": 22.5, "minutes_used": 780,
    },
}

PTCL_LANDLINE_BILLS = {
    "PTCL-LL-042-1234567": {
        "consumer_name": "Muhammad Ali",
        "landline_number": "042-1234567",
        "package": "Residential PSTN",
        "amount_due": 450.00,
        "bill_month": "March 2026", "due_date": "2026-04-15",
        "status": "unpaid", "surcharge": 0.00,
        "minutes_used": 120,
    },
}

# ── GOVERNMENT BILLS (FBR / Traffic Challans / Excise) ───────────────────────

FBR_BILLS = {
    "FBR-2026-STR-001234": {
        "challan_type": "Sales Tax Return",
        "taxpayer_name": "Alpha Trading Pvt Ltd",
        "ntn": "7654321-0",
        "strn": "12-00-1234-005-62",
        "amount_due": 45000.00,
        "tax_year": "2026", "due_date": "2026-04-18",
        "status": "unpaid", "surcharge": 0.00,
        "consumer_name": "Alpha Trading Pvt Ltd",
    },
    "FBR-2026-INC-005678": {
        "challan_type": "Income Tax - Advance",
        "taxpayer_name": "Sara Khan",
        "ntn": "9876543-2",
        "amount_due": 8000.00,
        "tax_year": "2026", "due_date": "2026-04-25",
        "status": "unpaid", "surcharge": 0.00,
        "consumer_name": "Sara Khan",
    },
}

EXCISE_BILLS = {
    "EXC-PJB-2026-70001": {
        "challan_type": "Vehicle Token Tax",
        "owner_name": "Muhammad Ali Khan",
        "registration_number": "LEB-1234",
        "vehicle_type": "Car", "engine_cc": 1300,
        "amount_due": 3000.00,
        "due_date": "2026-06-30", "status": "unpaid", "surcharge": 0.00,
        "consumer_name": "Muhammad Ali Khan",
    },
    "EXC-PJB-2026-70002": {
        "challan_type": "Vehicle Token Tax",
        "owner_name": "Omar Farooq",
        "registration_number": "LZP-5678",
        "vehicle_type": "Motorcycle", "engine_cc": 125,
        "amount_due": 800.00,
        "due_date": "2026-06-30", "status": "unpaid", "surcharge": 0.00,
        "consumer_name": "Omar Farooq",
    },
}

TRAFFIC_CHALLANS = {
    "CHN-LHR-2026-0011": {
        "challan_type": "Traffic Violation",
        "owner_name": "Hamza Shah",
        "registration_number": "LEA-9012",
        "violation": "Signal jumping",
        "issued_date": "2026-03-15",
        "amount_due": 500.00,
        "due_date": "2026-04-30", "status": "unpaid", "surcharge": 0.00,
        "consumer_name": "Hamza Shah",
    },
}

# ── LINKED BANK ACCOUNTS (for NayaPay / UPay / SadaPay) ──────────────────────
# Seeded linked bank accounts per mobile number (start empty, populated at runtime)

NAYAPAY_LINKED_BANKS: dict[str, list[dict]] = {}
UPAY_LINKED_BANKS:    dict[str, list[dict]] = {}
SADAPAY_LINKED_BANKS: dict[str, list[dict]] = {}

# 1LINK Bank Codes Reference
BANK_CODES = {
    "HBL":  "Habib Bank Limited",
    "MCB":  "MCB Bank Limited",
    "UBL":  "United Bank Limited",
    "MEZN": "Meezan Bank Limited",
    "ALFH": "Bank Alfalah Limited",
    "ABL":  "Allied Bank Limited",
    "NBP":  "National Bank of Pakistan",
    "SNBL": "Soneri Bank Limited",
    "BAHL": "Bank AL Habib Limited",
    "JSBL": "JS Bank Limited",
    "SILK": "Silkbank Limited",
    "FAYS": "Faysal Bank Limited",
    "SCBL": "Standard Chartered Pakistan",
    "SCBK": "Summit Bank Limited",
    "DUIB": "Dubai Islamic Bank Pakistan",
    "ZTBL": "Zarai Taraqiati Bank",
}
