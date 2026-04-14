"""NayaPay Mock Server — SQLite-backed, persists across restarts."""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from shared.mock_data import NAYAPAY_USERS, BANK_CODES, normalize_pk_phone
from shared.mock_db import WalletDB

DB_PATH = os.path.join(os.path.dirname(__file__), "nayapay.db")

app = FastAPI(title="NayaPay Mock Server", version="2.0.0")
db = WalletDB(db_path=DB_PATH, seed_data=NAYAPAY_USERS)


class AccountLookupRequest(BaseModel):
    mobile_number: str

class BankLinkRequest(BaseModel):
    mobile_number: str
    bank_code: str
    account_number: str
    account_title: str

class BankWithdrawRequest(BaseModel):
    mobile_number: str
    bank_code: str
    account_number: str
    amount: float
    transaction_ref: str

class TransferRequest(BaseModel):
    sender_mobile: str
    receiver_mobile: str
    amount: float
    transaction_ref: str
    description: Optional[str] = ""


@app.get("/health")
async def health():
    return {"status": "ok", "server": "NayaPay Mock", "port": 9003, "db": DB_PATH}


@app.post("/v1/accounts/lookup")
async def lookup_account(req: AccountLookupRequest):
    """Verify NayaPay account exists."""
    await asyncio.sleep(0.5)

    user = db.get(normalize_pk_phone(req.mobile_number))
    if not user:
        return {
            "status": "error",
            "code": "USER_NOT_FOUND",
            "success": False,
            "error_code": "ACCOUNT_NOT_FOUND",
            "message": "No NayaPay account registered with this number",
        }

    if user["status"] != "active":
        return {
            "status": "error",
            "code": "ACCOUNT_SUSPENDED",
            "success": False,
            "error_code": "ACCOUNT_SUSPENDED",
            "message": "NayaPay account is suspended",
        }

    return {
        "status": "success",
        "success": True,
        "data": {
            "display_name": user["name"],
            "mobile": req.mobile_number,
            "tier": user.get("tier", "lite"),
        },
    }


@app.post("/v1/transfers/send")
async def send_money(req: TransferRequest):
    """Send money to a NayaPay account."""
    await asyncio.sleep(1.1)

    receiver_mobile = normalize_pk_phone(req.receiver_mobile)
    receiver = db.get(receiver_mobile)
    if not receiver:
        return {"status": "error", "code": "RECEIVER_NOT_FOUND", "success": False,
                "error_code": "RECEIVER_NOT_FOUND", "message": "Receiver not found on NayaPay"}

    if receiver["status"] != "active":
        return {"status": "error", "code": "RECEIVER_SUSPENDED", "success": False,
                "error_code": "RECEIVER_BLOCKED", "message": "Receiver account is suspended"}

    remaining_limit = receiver["daily_limit"] - receiver["daily_sent"]
    if req.amount > remaining_limit:
        return {"status": "error", "code": "DAILY_LIMIT_EXCEEDED", "success": False,
                "error_code": "RECEIVER_LIMIT_EXCEEDED", "message": "Receiver daily transfer limit exceeded"}

    db.credit(receiver_mobile, req.amount)
    db.add_daily_sent(receiver_mobile, req.amount)

    txn_id = f"NP{uuid4().hex[:12].upper()}"

    return {
        "status": "success",
        "success": True,
        "data": {
            "transaction_id": txn_id,
            "source_ref": req.transaction_ref,
            "amount": req.amount,
            "currency": "PKR",
            "receiver_mobile": req.receiver_mobile,
            "receiver_name": receiver["name"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "message": f"PKR {req.amount:,.2f} sent to {receiver['name']} via NayaPay",
        },
    }


# ── BANK ACCOUNT ENDPOINTS ───────────────────────────────────────────────────

@app.post("/v1/accounts/bank/link")
async def link_bank_account(req: BankLinkRequest):
    """Link a bank account to a NayaPay wallet."""
    await asyncio.sleep(0.5)

    user = db.get(normalize_pk_phone(req.mobile_number))
    if not user:
        return {"success": False, "error_code": "USER_NOT_FOUND",
                "message": "No NayaPay account found for this mobile number."}

    bank_name = BANK_CODES.get(req.bank_code.upper())
    if not bank_name:
        return {"success": False, "error_code": "BANK_NOT_SUPPORTED",
                "message": f"Bank code '{req.bank_code}' is not supported."}

    entry = {
        "bank_code": req.bank_code.upper(),
        "bank_name": bank_name,
        "account_number": req.account_number,
        "account_title": req.account_title,
        "linked_at": datetime.now(timezone.utc).isoformat(),
        "is_verified": True,
    }
    if not db.add_linked_bank(req.mobile_number, entry):
        return {"success": False, "error_code": "ACCOUNT_ALREADY_LINKED",
                "message": "This bank account is already linked to your NayaPay wallet."}

    return {"success": True, "message": "Bank account linked successfully.",
            "linked_account": entry}


@app.get("/v1/accounts/bank")
async def list_bank_accounts(mobile_number: str):
    """List all bank accounts linked to a NayaPay wallet."""
    await asyncio.sleep(0.3)

    if not db.get(normalize_pk_phone(mobile_number)):
        return {"success": False, "error_code": "USER_NOT_FOUND",
                "message": "No NayaPay account found for this mobile number."}

    accounts = db.get_linked_banks(mobile_number)
    return {"success": True, "count": len(accounts), "accounts": accounts}


@app.post("/v1/accounts/bank/withdraw")
async def withdraw_to_bank(req: BankWithdrawRequest):
    """Withdraw funds from NayaPay wallet to a linked bank account."""
    await asyncio.sleep(1.4)

    user_mobile = normalize_pk_phone(req.mobile_number)
    user = db.get(user_mobile)
    if not user:
        return {"success": False, "error_code": "USER_NOT_FOUND",
                "message": "NayaPay account not found."}

    if user["status"] != "active":
        return {"success": False, "error_code": "ACCOUNT_SUSPENDED",
                "message": "Your NayaPay account is suspended."}

    accounts = db.get_linked_banks(req.mobile_number)
    linked = next((a for a in accounts if a["account_number"] == req.account_number
                   and a["bank_code"] == req.bank_code.upper()), None)
    if not linked:
        return {"success": False, "error_code": "BANK_ACCOUNT_NOT_LINKED",
                "message": "This bank account is not linked to your NayaPay wallet."}

    if user["balance"] < req.amount:
        return {"success": False, "error_code": "INSUFFICIENT_BALANCE",
                "message": f"Insufficient NayaPay balance. Available: PKR {user['balance']:,.2f}"}

    db.debit(user_mobile, req.amount)
    txn_id = f"NP-WD-{uuid4().hex[:10].upper()}"

    return {
        "success": True,
        "transaction_id": txn_id,
        "source_ref": req.transaction_ref,
        "amount": req.amount,
        "currency": "PKR",
        "destination_bank": linked["bank_name"],
        "destination_account": req.account_number,
        "destination_title": linked["account_title"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "estimated_credit": "1-2 business days",
        "status": "processing",
        "message": f"PKR {req.amount:,.2f} withdrawal to {linked['bank_name']} initiated.",
    }


@app.get("/api/balances")
async def all_balances():
    """Debug: show all NayaPay account balances."""
    return db.all_balances()


@app.post("/reset")
async def reset():
    """Dev/demo: reset all accounts and linked banks back to original state."""
    db.reset_all()
    return {"success": True, "message": "NayaPay accounts reset to original state."}


@app.post("/reset-daily")
async def reset_daily():
    """Dev/demo: reset daily_sent counters (simulate midnight)."""
    db.reset_daily_sent()
    return {"success": True, "message": "Daily sent counters reset to 0."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9003)
