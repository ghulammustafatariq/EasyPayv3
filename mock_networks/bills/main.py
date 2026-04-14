"""
Utility Bills Mock Server — SQLite3-backed, all 6 categories in one place.
  Electricity : LESCO, MEPCO, IESCO, PESCO, HESCO, QESCO, GEPCO, SEPCO
  Gas         : SNGPL, SSGC
  Water       : KWSB, WASA_LHR, WASA_RWP, WASA_FSD
  Internet    : PTCL, STORMFIBER, NAYATEL, TRANSWORLD, CYBERNET
  Phone       : JAZZ, ZONG, UFONE, TELENOR, PTCL_LL
  Government  : PSID, FBR, EXCISE, TRAFFIC

Bills are persisted in bills.db — paid status survives server restarts.
To reset all bills back to unpaid, delete bills.db and restart the server.

API:
  GET  /health
  GET  /companies                  — list all supported companies with category
  POST /inquiry                    — fetch bill details
  POST /pay                        — pay a bill
  POST /reset                      — reset a specific bill back to unpaid (dev tool)
"""
import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from shared.mock_data import (
    # Electricity
    LESCO_BILLS, MEPCO_BILLS, IESCO_BILLS, PESCO_BILLS,
    HESCO_BILLS, QESCO_BILLS, GEPCO_BILLS, SEPCO_BILLS,
    # Gas
    SNGPL_BILLS, SSGC_BILLS,
    # Water
    KWSB_BILLS, WASA_LHR_BILLS, WASA_RWP_BILLS, WASA_FSD_BILLS,
    # Internet
    PTCL_BILLS, STORMFIBER_BILLS, NAYATEL_BILLS, TRANSWORLD_BILLS, CYBERNET_BILLS,
    # Phone
    JAZZ_BILLS, ZONG_BILLS, UFONE_BILLS, TELENOR_BILLS, PTCL_LANDLINE_BILLS,
    # Government
    PSID_RECORDS, FBR_BILLS, EXCISE_BILLS, TRAFFIC_CHALLANS,
)

# ── DATABASE PATH ─────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "bills.db")

# ── COMPANY METADATA (static — category + display name, no bill data) ─────────
_COMPANY_META: dict[str, tuple[str, str]] = {
    "LESCO":      ("electricity", "Lahore Electric Supply Company"),
    "MEPCO":      ("electricity", "Multan Electric Power Company"),
    "IESCO":      ("electricity", "Islamabad Electric Supply Company"),
    "PESCO":      ("electricity", "Peshawar Electric Supply Company"),
    "HESCO":      ("electricity", "Hyderabad Electric Supply Company"),
    "QESCO":      ("electricity", "Quetta Electric Supply Company"),
    "GEPCO":      ("electricity", "Gujranwala Electric Power Company"),
    "SEPCO":      ("electricity", "Sukkur Electric Power Company"),
    "SNGPL":      ("gas",         "Sui Northern Gas Pipelines Limited"),
    "SSGC":       ("gas",         "Sui Southern Gas Company"),
    "KWSB":       ("water",       "Karachi Water & Sewerage Board"),
    "WASA_LHR":   ("water",       "WASA Lahore"),
    "WASA_RWP":   ("water",       "WASA Rawalpindi"),
    "WASA_FSD":   ("water",       "WASA Faisalabad"),
    "PTCL":       ("internet",    "PTCL Broadband"),
    "STORMFIBER": ("internet",    "StormFiber"),
    "NAYATEL":    ("internet",    "Nayatel"),
    "TRANSWORLD": ("internet",    "Transworld"),
    "CYBERNET":   ("internet",    "Cybernet"),
    "JAZZ":       ("phone",       "Jazz Postpaid"),
    "ZONG":       ("phone",       "Zong Postpaid"),
    "UFONE":      ("phone",       "Ufone Postpaid"),
    "TELENOR":    ("phone",       "Telenor Postpaid"),
    "PTCL_LL":    ("phone",       "PTCL Landline"),
    "PSID":       ("government",  "Tax Payment (PSID/FBR)"),
    "FBR":        ("government",  "Federal Board of Revenue"),
    "EXCISE":     ("government",  "Excise & Taxation (Vehicle Tax)"),
    "TRAFFIC":    ("government",  "Traffic Police (Challans)"),
}

# ── SEED DATA (used only when bills.db doesn't exist or is empty) ──────────────
_SEED_DATA: dict[str, dict] = {
    "LESCO": LESCO_BILLS, "MEPCO": MEPCO_BILLS, "IESCO": IESCO_BILLS,
    "PESCO": PESCO_BILLS, "HESCO": HESCO_BILLS, "QESCO": QESCO_BILLS,
    "GEPCO": GEPCO_BILLS, "SEPCO": SEPCO_BILLS,
    "SNGPL": SNGPL_BILLS, "SSGC": SSGC_BILLS,
    "KWSB": KWSB_BILLS, "WASA_LHR": WASA_LHR_BILLS,
    "WASA_RWP": WASA_RWP_BILLS, "WASA_FSD": WASA_FSD_BILLS,
    "PTCL": PTCL_BILLS, "STORMFIBER": STORMFIBER_BILLS,
    "NAYATEL": NAYATEL_BILLS, "TRANSWORLD": TRANSWORLD_BILLS,
    "CYBERNET": CYBERNET_BILLS,
    "JAZZ": JAZZ_BILLS, "ZONG": ZONG_BILLS, "UFONE": UFONE_BILLS,
    "TELENOR": TELENOR_BILLS, "PTCL_LL": PTCL_LANDLINE_BILLS,
    "PSID": PSID_RECORDS, "FBR": FBR_BILLS,
    "EXCISE": EXCISE_BILLS, "TRAFFIC": TRAFFIC_CHALLANS,
}

# ── "MAIN" COLUMNS stored explicitly; everything else goes in extra_json ───────
_MAIN_KEYS = {
    "consumer_name", "taxpayer_name", "owner_name",
    "amount_due", "surcharge", "due_date", "bill_month", "status",
}


# ── SQLite HELPERS ────────────────────────────────────────────────────────────

@contextmanager
def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db() -> None:
    """Create the bills table and seed from mock_data if the DB is empty."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                company         TEXT NOT NULL,
                consumer_number TEXT NOT NULL,
                consumer_name   TEXT,
                amount_due      REAL NOT NULL DEFAULT 0,
                surcharge       REAL NOT NULL DEFAULT 0,
                due_date        TEXT,
                bill_month      TEXT,
                status          TEXT NOT NULL DEFAULT 'unpaid',
                paid_at         TEXT,
                payment_ref     TEXT,
                extra_json      TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (company, consumer_number)
            )
        """)

        # Only seed if completely empty
        count = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
        if count > 0:
            return

        for company, bills_dict in _SEED_DATA.items():
            for consumer_number, bill in bills_dict.items():
                consumer_name = (
                    bill.get("consumer_name")
                    or bill.get("taxpayer_name")
                    or bill.get("owner_name")
                    or "Consumer"
                )
                extra = {k: v for k, v in bill.items() if k not in _MAIN_KEYS}
                conn.execute(
                    """
                    INSERT OR IGNORE INTO bills
                        (company, consumer_number, consumer_name, amount_due,
                         surcharge, due_date, bill_month, status, extra_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company,
                        consumer_number,
                        consumer_name,
                        float(bill.get("amount_due", 0)),
                        float(bill.get("surcharge", 0)),
                        bill.get("due_date", ""),
                        bill.get("bill_month", ""),
                        bill.get("status", "unpaid"),
                        json.dumps(extra),
                    ),
                )


app = FastAPI(title="Utility Bills Mock Server", version="2.0.0")


@app.on_event("startup")
async def startup():
    _init_db()


# ── REQUEST MODELS ────────────────────────────────────────────────────────────

class BillInquiryRequest(BaseModel):
    company: str
    consumer_number: str

class BillPayRequest(BaseModel):
    company: str
    consumer_number: str
    amount: float
    transaction_ref: str

class BillResetRequest(BaseModel):
    company: str
    consumer_number: str


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "server": "Utility Bills Mock", "port": 9006, "db": DB_PATH}


@app.get("/companies")
async def list_companies():
    """Return all supported bill companies grouped by category."""
    grouped: dict[str, list] = {}
    for code, (category, name) in _COMPANY_META.items():
        grouped.setdefault(category, []).append({"code": code, "name": name})
    return {
        "categories": [
            {"category": cat, "companies": grouped[cat]}
            for cat in ["electricity", "gas", "water", "internet", "phone", "government"]
            if cat in grouped
        ]
    }


@app.post("/inquiry")
async def bill_inquiry(req: BillInquiryRequest):
    """Fetch bill details — reads live status from SQLite."""
    await asyncio.sleep(0.9)

    company = req.company.upper()
    meta = _COMPANY_META.get(company)
    if not meta:
        return {
            "success": False,
            "error_code": "COMPANY_NOT_SUPPORTED",
            "message": f"'{company}' is not a supported company. Call GET /companies for the full list.",
        }

    category, display_name = meta

    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM bills WHERE company = ? AND consumer_number = ?",
            (company, req.consumer_number),
        ).fetchone()

    if not row:
        return {
            "success": False,
            "error_code": "CONSUMER_NOT_FOUND",
            "message": f"No record found for consumer '{req.consumer_number}' at {display_name}.",
        }

    extra = json.loads(row["extra_json"] or "{}")

    return {
        "success": True,
        "company": company,
        "company_name": display_name,
        "category": category,
        "consumer_number": req.consumer_number,
        "consumer_name": row["consumer_name"],
        "amount_due": row["amount_due"],
        "due_date": row["due_date"],
        "bill_month": row["bill_month"] or "",
        "status": row["status"],
        "surcharge": row["surcharge"],
        "total_payable": row["amount_due"] + row["surcharge"],
        "paid_at": row["paid_at"],
        "payment_ref": row["payment_ref"],
        # Category-specific extras
        "units_consumed": extra.get("units_consumed"),
        "package": extra.get("package"),
        "connection_type": extra.get("connection_type"),
        "data_used_gb": extra.get("data_used_gb"),
        "minutes_used": extra.get("minutes_used"),
        "mobile_number": extra.get("mobile_number"),
        "vehicle_info": (
            f"{extra.get('registration_number')} — {extra.get('vehicle_type', '')}"
            if extra.get("registration_number") else None
        ),
        "challan_type": extra.get("challan_type"),
        "address": extra.get("address", ""),
    }


@app.post("/pay")
async def bill_pay(req: BillPayRequest):
    """Pay a bill. Updates status to 'paid' in SQLite — persists across restarts."""
    await asyncio.sleep(1.2)

    company = req.company.upper()
    meta = _COMPANY_META.get(company)
    if not meta:
        return {
            "success": False,
            "error_code": "COMPANY_NOT_SUPPORTED",
            "message": f"'{company}' is not a supported company.",
        }

    category, display_name = meta

    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM bills WHERE company = ? AND consumer_number = ?",
            (company, req.consumer_number),
        ).fetchone()

        if not row:
            return {
                "success": False,
                "error_code": "CONSUMER_NOT_FOUND",
                "message": f"Consumer '{req.consumer_number}' not found at {display_name}.",
            }

        if row["status"] == "paid":
            return {
                "success": False,
                "error_code": "ALREADY_PAID",
                "message": (
                    f"This bill was already paid on {row['paid_at'] or 'a previous date'}. "
                    "Ref: " + (row["payment_ref"] or "N/A")
                ),
            }

        total_payable = row["amount_due"] + row["surcharge"]
        if abs(req.amount - total_payable) > 1.00:
            return {
                "success": False,
                "error_code": "AMOUNT_MISMATCH",
                "message": (
                    f"Payment amount PKR {req.amount:,.2f} does not match "
                    f"payable amount PKR {total_payable:,.2f}."
                ),
            }

        paid_at = datetime.now(timezone.utc).isoformat()
        receipt_ref = f"BILL{uuid4().hex[:10].upper()}"

        # ── Persist payment to SQLite ──────────────────────────────────────────
        conn.execute(
            """
            UPDATE bills
            SET status = 'paid', paid_at = ?, payment_ref = ?
            WHERE company = ? AND consumer_number = ?
            """,
            (paid_at, req.transaction_ref, company, req.consumer_number),
        )

    return {
        "success": True,
        "receipt_reference": receipt_ref,
        "source_ref": req.transaction_ref,
        "company": company,
        "company_name": display_name,
        "category": category,
        "consumer_number": req.consumer_number,
        "consumer_name": row["consumer_name"],
        "amount_paid": req.amount,
        "paid_at": paid_at,
        "status": "PAID",
        "message": f"{display_name} bill paid successfully.",
    }


@app.post("/reset")
async def reset_bill(req: BillResetRequest):
    """
    Dev/demo tool — reset a specific bill back to unpaid.
    Useful for re-testing the same consumer number in a presentation.
    """
    company = req.company.upper()
    if company not in _COMPANY_META:
        return {"success": False, "error_code": "COMPANY_NOT_SUPPORTED"}

    with _get_conn() as conn:
        result = conn.execute(
            """
            UPDATE bills
            SET status = 'unpaid', paid_at = NULL, payment_ref = NULL
            WHERE company = ? AND consumer_number = ?
            """,
            (company, req.consumer_number),
        )
        if result.rowcount == 0:
            return {"success": False, "error_code": "CONSUMER_NOT_FOUND"}

    return {
        "success": True,
        "message": f"Bill for {req.consumer_number} at {company} reset to unpaid.",
    }


@app.post("/reset-all")
async def reset_all_bills():
    """
    Dev/demo tool — reset ALL bills back to unpaid.
    Same as deleting bills.db and restarting, but without the restart.
    """
    with _get_conn() as conn:
        conn.execute("UPDATE bills SET status = 'unpaid', paid_at = NULL, payment_ref = NULL")
        # Also fix overdue bills back to their original status from seed
        for company, bills_dict in _SEED_DATA.items():
            for consumer_number, bill in bills_dict.items():
                original_status = bill.get("status", "unpaid")
                conn.execute(
                    "UPDATE bills SET status = ? WHERE company = ? AND consumer_number = ?",
                    (original_status, company, consumer_number),
                )

    return {"success": True, "message": "All bills reset to original unpaid/overdue status."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9006)
