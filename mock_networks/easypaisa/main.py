"""Easypaisa Mock Server — SQLite-backed, persists across restarts."""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from shared.mock_data import EASYPAISA_USERS, normalize_pk_phone
from shared.mock_db import WalletDB

DB_PATH = os.path.join(os.path.dirname(__file__), "easypaisa.db")

app = FastAPI(title="Easypaisa Mock Server", version="2.0.0")
db = WalletDB(db_path=DB_PATH, seed_data=EASYPAISA_USERS)


class AccountLookupRequest(BaseModel):
    mobile_number: str

class TransferRequest(BaseModel):
    sender_mobile: str
    receiver_mobile: str
    amount: float
    transaction_ref: str
    description: Optional[str] = ""

class TopupCollectRequest(BaseModel):
    mobile_number: str
    amount: float
    transaction_ref: str


@app.get("/health")
async def health():
    return {"status": "ok", "server": "Easypaisa Mock", "port": 9002, "db": DB_PATH}


@app.post("/MWALLET/account/lookup")
async def lookup_account(req: AccountLookupRequest):
    """Verify Easypaisa account exists."""
    await asyncio.sleep(0.7)

    user = db.get(normalize_pk_phone(req.mobile_number))
    if not user:
        return {
            "responseCode": "400",
            "responseDesc": "CustomerNotFound",
            "success": False,
            "error_code": "ACCOUNT_NOT_FOUND",
        }

    if user["status"] != "active":
        return {
            "responseCode": "403",
            "responseDesc": "AccountBlocked",
            "success": False,
            "error_code": "ACCOUNT_BLOCKED",
        }

    return {
        "responseCode": "200",
        "responseDesc": "Success",
        "success": True,
        "customerName": user["name"],
        "mobileNumber": req.mobile_number,
        "accountType": user.get("account_type", "MWALLET"),
    }


@app.post("/MWALLET/account/sendmoney")
async def send_money(req: TransferRequest):
    """Send money to an Easypaisa mobile account."""
    await asyncio.sleep(1.4)

    receiver_mobile = normalize_pk_phone(req.receiver_mobile)
    receiver = db.get(receiver_mobile)
    if not receiver:
        return {
            "responseCode": "400",
            "responseDesc": "ReceiverNotFound",
            "success": False,
            "error_code": "RECEIVER_NOT_FOUND",
        }

    if receiver["status"] != "active":
        return {
            "responseCode": "403",
            "responseDesc": "ReceiverAccountBlocked",
            "success": False,
            "error_code": "RECEIVER_BLOCKED",
        }

    remaining_limit = receiver["daily_limit"] - receiver["daily_sent"]
    if req.amount > remaining_limit:
        return {
            "responseCode": "429",
            "responseDesc": "DailyLimitExceeded",
            "success": False,
            "error_code": "RECEIVER_LIMIT_EXCEEDED",
        }

    db.credit(receiver_mobile, req.amount)
    db.add_daily_sent(receiver_mobile, req.amount)

    txn_id = f"EP{uuid4().hex[:12].upper()}"

    return {
        "responseCode": "200",
        "responseDesc": "TransactionSuccessful",
        "success": True,
        "transactionId": txn_id,
        "sourceRef": req.transaction_ref,
        "amount": req.amount,
        "receiverMobile": req.receiver_mobile,
        "receiverName": receiver["name"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": f"PKR {req.amount:,.2f} sent to {receiver['name']} via Easypaisa",
    }


@app.post("/MWALLET/account/topup")
async def collect_topup(req: TopupCollectRequest):
    """Simulate merchant collecting payment from a customer's Easypaisa wallet."""
    await asyncio.sleep(1.4)

    user_mobile = normalize_pk_phone(req.mobile_number)
    user = db.get(user_mobile)
    if not user:
        return {
            "responseCode": "400",
            "responseDesc": "CustomerNotFound",
            "success": False,
            "error_code": "ACCOUNT_NOT_FOUND",
        }

    if user["status"] != "active":
        return {
            "responseCode": "403",
            "responseDesc": "AccountBlocked",
            "success": False,
            "error_code": "ACCOUNT_BLOCKED",
        }

    if user["balance"] < req.amount:
        return {
            "responseCode": "402",
            "responseDesc": "InsufficientBalance",
            "success": False,
            "error_code": "INSUFFICIENT_BALANCE",
        }

    db.debit(user_mobile, req.amount)
    txn_id = f"EP{uuid4().hex[:12].upper()}"

    return {
        "responseCode": "200",
        "responseDesc": "TransactionSuccessful",
        "success": True,
        "transactionId": txn_id,
        "orderRefNum": req.transaction_ref,
        "amount": req.amount,
        "mobileAccountNo": req.mobile_number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": f"PKR {req.amount:,.2f} collected from {user['name']} via Easypaisa",
    }


@app.get("/api/balances")
async def all_balances():
    """Debug: show all Easypaisa account balances."""
    return db.all_balances()


@app.post("/reset")
async def reset():
    """Dev/demo: reset all accounts back to original seed balances."""
    db.reset_all()
    return {"success": True, "message": "Easypaisa accounts reset to original state."}


@app.post("/reset-daily")
async def reset_daily():
    """Dev/demo: reset daily_sent counters (simulate midnight)."""
    db.reset_daily_sent()
    return {"success": True, "message": "Daily sent counters reset to 0."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9002)
