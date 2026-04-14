"""
1LINK Mock Server — IBFT (Interbank Fund Transfer) only.
Bill payments have been moved to the dedicated Bills server (port 9006).

Endpoints:
  GET  /health
  GET  /banks
  POST /ibft/lookup    — verify destination account before transfer
  POST /ibft/transfer  — execute the actual IBFT
"""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from shared.mock_data import BANK_ACCOUNTS, BANK_CODES
from shared.mock_db import BankAccountDB

DB_PATH = os.path.join(os.path.dirname(__file__), "onelink.db")

app = FastAPI(title="1LINK IBFT Mock Server", version="2.0.0")
db = BankAccountDB(db_path=DB_PATH, seed_data=BANK_ACCOUNTS)


# ── REQUEST MODELS ────────────────────────────────────────────────────────────

class IBFTLookupRequest(BaseModel):
    bank_code: str
    account_number: str

class IBFTTransferRequest(BaseModel):
    source_bank: str
    source_account: str
    dest_bank_code: str
    dest_account_number: str
    amount: float
    transaction_ref: str
    remarks: Optional[str] = ""


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "server": "1LINK IBFT Mock", "port": 9000, "db": DB_PATH}

@app.get("/banks")
async def get_bank_list():
    return {"banks": [{"code": k, "name": v} for k, v in BANK_CODES.items()]}


@app.post("/ibft/lookup")
async def ibft_lookup(req: IBFTLookupRequest):
    await asyncio.sleep(0.8)
    account = db.get(req.bank_code, req.account_number)
    if not account:
        return {"success": False, "error_code": "ACCOUNT_NOT_FOUND",
                "message": f"No account found at {req.bank_code} for account {req.account_number}."}
    if account["status"] != "active":
        return {"success": False, "error_code": "ACCOUNT_INACTIVE",
                "message": "This bank account is not active."}
    return {"success": True, "account_title": account["account_title"],
            "bank_name": account["bank_name"], "bank_code": account["bank_code"],
            "account_number": req.account_number}


@app.post("/ibft/transfer")
async def ibft_transfer(req: IBFTTransferRequest):
    await asyncio.sleep(1.5)
    dest_account = db.get(req.dest_bank_code, req.dest_account_number)
    if not dest_account:
        return {"success": False, "error_code": "DESTINATION_NOT_FOUND",
                "message": f"Account {req.dest_account_number} not found at {req.dest_bank_code}."}
    if dest_account["status"] != "active":
        return {"success": False, "error_code": "DESTINATION_INACTIVE",
                "message": "Destination bank account is not active."}
    db.credit(req.dest_bank_code, req.dest_account_number, req.amount)
    stan = uuid4().hex[:12].upper()
    rrn  = f"1LNK{uuid4().hex[:8].upper()}"
    return {
        "success": True, "stan": stan, "rrn": rrn,
        "source_ref": req.transaction_ref,
        "dest_bank": req.dest_bank_code.upper(),
        "dest_account": req.dest_account_number,
        "dest_account_title": dest_account["account_title"],
        "amount": req.amount, "currency": "PKR", "status": "COMPLETED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": f"PKR {req.amount:,.2f} credited to {dest_account['account_title']} at {dest_account['bank_name']}.",
    }


@app.post("/reset")
async def reset():
    """Dev/demo: reset all bank account balances to original state."""
    db.reset_all()
    return {"success": True, "message": "All 1LINK bank accounts reset to original state."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
