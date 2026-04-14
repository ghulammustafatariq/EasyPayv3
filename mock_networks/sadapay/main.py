"""SadaPay Mock Server — SQLite-backed, persists across restarts."""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from shared.mock_data import SADAPAY_USERS, BANK_CODES, normalize_pk_phone
from shared.mock_db import WalletDB

DB_PATH = os.path.join(os.path.dirname(__file__), "sadapay.db")

app = FastAPI(title="SadaPay Mock Server", version="2.0.0")
db = WalletDB(db_path=DB_PATH, seed_data=SADAPAY_USERS)


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
    return {"status": "ok", "server": "SadaPay Mock", "port": 9005, "db": DB_PATH}


@app.post("/v2/wallets/lookup")
async def lookup_account(req: AccountLookupRequest):
    """Look up a SadaPay wallet account."""
    await asyncio.sleep(0.5)

    user = db.get(normalize_pk_phone(req.mobile_number))
    if not user:
        return {
            "success": False,
            "error": {
                "code": "WALLET_NOT_FOUND",
                "description": "No SadaPay wallet linked to this mobile number",
            },
        }

    if user["status"] != "active":
        return {
            "success": False,
            "error": {
                "code": "WALLET_SUSPENDED",
                "description": "SadaPay wallet is suspended",
            },
        }

    return {
        "success": True,
        "wallet": {
            "display_name": user["name"],
            "mobile": req.mobile_number,
            "wallet_type": user.get("wallet_type", "personal"),
        },
    }


@app.post("/v2/transfers")
async def send_money(req: TransferRequest):
    """Send money to a SadaPay wallet."""
    await asyncio.sleep(1.0)

    receiver_mobile = normalize_pk_phone(req.receiver_mobile)
    receiver = db.get(receiver_mobile)
    if not receiver:
        return {
            "success": False,
            "error": {
                "code": "RECEIVER_NOT_FOUND",
                "description": "Receiver's SadaPay wallet not found",
            },
        }

    if receiver["status"] != "active":
        return {
            "success": False,
            "error": {
                "code": "RECEIVER_SUSPENDED",
                "description": "Receiver's SadaPay wallet is suspended",
            },
        }

    remaining_limit = receiver["daily_limit"] - receiver["daily_sent"]
    if req.amount > remaining_limit:
        return {
            "success": False,
            "error": {
                "code": "DAILY_LIMIT_EXCEEDED",
                "description": f"Receiver daily limit exceeded. Remaining: PKR {remaining_limit:,.2f}",
            },
        }

    db.credit(receiver_mobile, req.amount)
    db.add_daily_sent(receiver_mobile, req.amount)

    txn_id = f"SP{uuid4().hex[:12].upper()}"

    return {
        "success": True,
        "transfer": {
            "id": txn_id,
            "source_ref": req.transaction_ref,
            "amount": req.amount,
            "currency": "PKR",
            "receiver": {
                "mobile": req.receiver_mobile,
                "name": receiver["name"],
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
        },
        "message": f"PKR {req.amount:,.2f} sent to {receiver['name']} via SadaPay",
    }


# ── BANK ACCOUNT ENDPOINTS ───────────────────────────────────────────────────

@app.post("/v2/bank-accounts/link")
async def link_bank_account(req: BankLinkRequest):
    """Link a bank account to a SadaPay wallet."""
    await asyncio.sleep(0.5)

    user = db.get(normalize_pk_phone(req.mobile_number))
    if not user:
        return {"success": False,
                "error": {"code": "USER_NOT_FOUND",
                          "description": "No SadaPay account found for this number."}}

    bank_name = BANK_CODES.get(req.bank_code.upper())
    if not bank_name:
        return {"success": False,
                "error": {"code": "BANK_NOT_SUPPORTED",
                          "description": f"'{req.bank_code}' is not supported."}}

    entry = {
        "bank_code": req.bank_code.upper(),
        "bank_name": bank_name,
        "account_number": req.account_number,
        "account_title": req.account_title,
        "linked_at": datetime.now(timezone.utc).isoformat(),
        "verified": True,
    }
    if not db.add_linked_bank(req.mobile_number, entry):
        return {"success": False,
                "error": {"code": "ALREADY_LINKED",
                          "description": "This bank account is already linked."}}
    return {"success": True, "bank_account": entry,
            "message": "Bank account linked to SadaPay successfully."}


@app.get("/v2/bank-accounts")
async def list_bank_accounts(mobile_number: str):
    """List bank accounts linked to a SadaPay wallet."""
    await asyncio.sleep(0.3)
    if not db.get(normalize_pk_phone(mobile_number)):
        return {"success": False,
                "error": {"code": "USER_NOT_FOUND", "description": "Account not found."}}
    accounts = db.get_linked_banks(mobile_number)
    return {"success": True, "count": len(accounts), "bank_accounts": accounts}


@app.post("/v2/bank-accounts/withdraw")
async def withdraw_to_bank(req: BankWithdrawRequest):
    """Withdraw funds from SadaPay to a linked bank account."""
    await asyncio.sleep(1.2)

    user_mobile = normalize_pk_phone(req.mobile_number)
    user = db.get(user_mobile)
    if not user:
        return {"success": False,
                "error": {"code": "USER_NOT_FOUND", "description": "SadaPay account not found."}}

    if user["status"] != "active":
        return {"success": False,
                "error": {"code": "ACCOUNT_SUSPENDED", "description": "Account is suspended."}}

    accounts = db.get_linked_banks(req.mobile_number)
    linked = next((a for a in accounts if a["account_number"] == req.account_number
                   and a["bank_code"] == req.bank_code.upper()), None)
    if not linked:
        return {"success": False,
                "error": {"code": "BANK_NOT_LINKED",
                          "description": "This bank account is not linked to your SadaPay wallet."}}

    if user["balance"] < req.amount:
        return {"success": False,
                "error": {"code": "INSUFFICIENT_BALANCE",
                          "description": f"Insufficient balance. Available: PKR {user['balance']:,.2f}"}}

    db.debit(user_mobile, req.amount)
    txn_id = f"SP-WD-{uuid4().hex[:10].upper()}"

    return {
        "success": True,
        "withdrawal": {
            "id": txn_id,
            "source_ref": req.transaction_ref,
            "amount": req.amount,
            "currency": "PKR",
            "destination": {
                "bank": linked["bank_name"],
                "account_number": req.account_number,
                "account_title": linked["account_title"],
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "estimated_credit": "1-2 business days",
            "status": "processing",
        },
        "message": f"PKR {req.amount:,.2f} withdrawal to {linked['bank_name']} initiated.",
    }


@app.get("/api/balances")
async def all_balances():
    """Debug: show all SadaPay account balances."""
    return db.all_balances()


@app.post("/reset")
async def reset():
    """Dev/demo: reset all accounts and linked banks back to original state."""
    db.reset_all()
    return {"success": True, "message": "SadaPay accounts reset to original state."}


@app.post("/reset-daily")
async def reset_daily():
    """Dev/demo: reset daily_sent counters (simulate midnight)."""
    db.reset_daily_sent()
    return {"success": True, "message": "Daily sent counters reset to 0."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9005)
