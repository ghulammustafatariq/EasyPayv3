"""JazzCash Mock Server — SQLite-backed, persists across restarts."""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from shared.mock_data import JAZZCASH_USERS, normalize_pk_phone
from shared.mock_db import WalletDB

DB_PATH = os.path.join(os.path.dirname(__file__), "jazzcash.db")

app = FastAPI(title="JazzCash Mock Server", version="2.0.0")
db = WalletDB(db_path=DB_PATH, seed_data=JAZZCASH_USERS)


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
    return {"status": "ok", "server": "JazzCash Mock", "port": 9001, "db": DB_PATH}


@app.post("/api/lookup")
async def lookup_account(req: AccountLookupRequest):
    """Verify JazzCash account exists before sending."""
    await asyncio.sleep(0.6)

    user = db.get(normalize_pk_phone(req.mobile_number))
    if not user:
        return {
            "pp_ResponseCode": "01",
            "pp_ResponseMessage": "Account not registered with JazzCash",
            "success": False,
            "error_code": "ACCOUNT_NOT_FOUND",
        }

    if user["status"] != "active":
        return {
            "pp_ResponseCode": "09",
            "pp_ResponseMessage": "Account is blocked",
            "success": False,
            "error_code": "ACCOUNT_BLOCKED",
        }

    return {
        "pp_ResponseCode": "000",
        "pp_ResponseMessage": "Account found",
        "success": True,
        "account_name": user["name"],
        "mobile_number": req.mobile_number,
        "account_level": user.get("account_level", "L1"),
    }


@app.post("/api/send")
async def send_money(req: TransferRequest):
    """Transfer funds to a JazzCash wallet."""
    await asyncio.sleep(1.3)

    receiver_mobile = normalize_pk_phone(req.receiver_mobile)
    receiver = db.get(receiver_mobile)
    if not receiver:
        return {
            "pp_ResponseCode": "01",
            "pp_ResponseMessage": "Receiver mobile not registered with JazzCash",
            "success": False,
            "error_code": "RECEIVER_NOT_FOUND",
        }

    if receiver["status"] != "active":
        return {
            "pp_ResponseCode": "09",
            "pp_ResponseMessage": "Receiver account is blocked",
            "success": False,
            "error_code": "RECEIVER_BLOCKED",
        }

    remaining_limit = receiver["daily_limit"] - receiver["daily_sent"]
    if req.amount > remaining_limit:
        return {
            "pp_ResponseCode": "14",
            "pp_ResponseMessage": "Receiver daily limit exceeded",
            "success": False,
            "error_code": "RECEIVER_LIMIT_EXCEEDED",
        }

    db.credit(receiver_mobile, req.amount)
    db.add_daily_sent(receiver_mobile, req.amount)

    txn_id = f"JC{uuid4().hex[:10].upper()}"

    return {
        "pp_ResponseCode": "000",
        "pp_ResponseMessage": "Transaction Successful",
        "success": True,
        "pp_TxnRefNo": txn_id,
        "pp_SourceRef": req.transaction_ref,
        "pp_Amount": str(int(req.amount * 100)),  # JazzCash uses paisas
        "pp_ReceiverMobile": req.receiver_mobile,
        "pp_ReceiverName": receiver["name"],
        "pp_TxnDateTime": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        "message": f"PKR {req.amount:,.2f} sent to {receiver['name']} on JazzCash",
    }


@app.post("/api/topup")
async def collect_topup(req: TopupCollectRequest):
    """Simulate merchant collecting payment from a customer's JazzCash wallet."""
    await asyncio.sleep(1.2)

    user_mobile = normalize_pk_phone(req.mobile_number)
    user = db.get(user_mobile)
    if not user:
        return {
            "pp_ResponseCode": "01",
            "pp_ResponseMessage": "Mobile number not registered with JazzCash",
            "success": False,
            "error_code": "ACCOUNT_NOT_FOUND",
        }

    if user["status"] != "active":
        return {
            "pp_ResponseCode": "09",
            "pp_ResponseMessage": "JazzCash account is blocked",
            "success": False,
            "error_code": "ACCOUNT_BLOCKED",
        }

    if user["balance"] < req.amount:
        return {
            "pp_ResponseCode": "15",
            "pp_ResponseMessage": "Insufficient balance in JazzCash account",
            "success": False,
            "error_code": "INSUFFICIENT_BALANCE",
        }

    db.debit(user_mobile, req.amount)
    txn_id = f"JC{uuid4().hex[:10].upper()}"

    return {
        "pp_ResponseCode": "000",
        "pp_ResponseMessage": "Transaction Successful",
        "success": True,
        "pp_TxnRefNo": txn_id,
        "pp_Amount": str(int(req.amount * 100)),
        "pp_TxnDateTime": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        "pp_MobileNumber": req.mobile_number,
        "message": f"PKR {req.amount:,.2f} collected from {user['name']} via JazzCash",
    }


@app.get("/api/balances")
async def all_balances():
    """Debug: show all JazzCash account balances."""
    return db.all_balances()


@app.post("/reset")
async def reset():
    """Dev/demo: reset all accounts back to original seed balances."""
    db.reset_all()
    return {"success": True, "message": "JazzCash accounts reset to original state."}


@app.post("/reset-daily")
async def reset_daily():
    """Dev/demo: reset daily_sent counters (simulate midnight)."""
    db.reset_daily_sent()
    return {"success": True, "message": "Daily sent counters reset to 0."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9001)
