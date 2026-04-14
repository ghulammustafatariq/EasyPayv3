"""
Shared SQLite persistence layer for all mock payment network servers.

Each server gets its own .db file in its own directory.
Data survives server restarts. Reset with POST /reset on each server.

Usage:
    from shared.mock_db import WalletDB, BankAccountDB

    # Wallet server (JazzCash, Easypaisa, NayaPay, SadaPay, UPay)
    db = WalletDB(db_path="/path/to/server.db", seed_data=JAZZCASH_USERS)
    user = db.get("+923001001001")        # → dict or None
    db.credit(mobile, amount)             # balance += amount (receiver)
    db.debit(mobile, amount)              # balance -= amount (sender/topup)
    db.add_daily_sent(mobile, amount)     # daily_sent += amount

    # Servers with linked banks (NayaPay, SadaPay, UPay)
    banks = db.get_linked_banks(mobile)   # → list[dict]
    db.add_linked_bank(mobile, entry)     # insert, returns False if duplicate

    # Reset for demos
    db.reset_all()                        # wipe + re-seed from original data

    # 1LINK / IBFT bank accounts
    bdb = BankAccountDB(db_path="...", seed_data=BANK_ACCOUNTS)
    acct = bdb.get(bank_code, account_number)  # → dict or None
    bdb.credit(bank_code, account_number, amount)
    bdb.reset_all()
"""

import json
import sqlite3
from contextlib import contextmanager

# Core columns stored explicitly; everything else → extra_json
_WALLET_CORE = {"name", "balance", "daily_limit", "daily_sent", "status"}
_BANK_CORE   = {"bank_code", "bank_name", "account_number", "account_title", "balance", "status"}


@contextmanager
def _conn(path: str):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


# ─────────────────────────────────────────────────────────────────────────────
# WalletDB — for JazzCash, Easypaisa, NayaPay, SadaPay, UPay
# ─────────────────────────────────────────────────────────────────────────────

class WalletDB:
    """SQLite-backed wallet user store with optional linked bank support."""

    def __init__(self, db_path: str, seed_data: dict):
        self._path = db_path
        self._seed = seed_data
        self._init()

    def _init(self):
        with _conn(self._path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    mobile      TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    balance     REAL NOT NULL DEFAULT 0,
                    daily_limit REAL NOT NULL DEFAULT 25000,
                    daily_sent  REAL NOT NULL DEFAULT 0,
                    status      TEXT NOT NULL DEFAULT 'active',
                    extra_json  TEXT NOT NULL DEFAULT '{}'
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS linked_banks (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    mobile         TEXT NOT NULL,
                    bank_code      TEXT NOT NULL,
                    bank_name      TEXT NOT NULL,
                    account_number TEXT NOT NULL,
                    account_title  TEXT NOT NULL,
                    linked_at      TEXT,
                    extra_json     TEXT NOT NULL DEFAULT '{}'
                )
            """)
            # Seed only when completely empty
            if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                self._seed_all(c)

    def _seed_all(self, c):
        for mobile, data in self._seed.items():
            extra = {k: v for k, v in data.items() if k not in _WALLET_CORE}
            c.execute(
                """INSERT OR IGNORE INTO users
                   (mobile, name, balance, daily_limit, daily_sent, status, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    mobile,
                    data.get("name", ""),
                    float(data.get("balance", 0)),
                    float(data.get("daily_limit", 25000)),
                    float(data.get("daily_sent", 0)),
                    data.get("status", "active"),
                    json.dumps(extra),
                ),
            )

    def _row_to_dict(self, row) -> dict:
        if row is None:
            return None
        d = dict(row)
        extra = json.loads(d.pop("extra_json", "{}"))
        d.update(extra)
        return d

    # ── READ ──────────────────────────────────────────────────────────────────

    def get(self, mobile: str) -> dict | None:
        with _conn(self._path) as c:
            row = c.execute(
                "SELECT * FROM users WHERE mobile = ?", (mobile,)
            ).fetchone()
        return self._row_to_dict(row)

    def all_balances(self) -> dict:
        with _conn(self._path) as c:
            rows = c.execute(
                "SELECT mobile, name, balance, daily_sent FROM users"
            ).fetchall()
        return {
            r["mobile"]: {"name": r["name"], "balance": r["balance"], "daily_sent": r["daily_sent"]}
            for r in rows
        }

    # ── WRITE ─────────────────────────────────────────────────────────────────

    def credit(self, mobile: str, amount: float):
        """Add to balance (receiver gets money / topup arrives)."""
        with _conn(self._path) as c:
            c.execute(
                "UPDATE users SET balance = balance + ? WHERE mobile = ?",
                (float(amount), mobile),
            )

    def debit(self, mobile: str, amount: float):
        """Subtract from balance (sender pays / topup deducted from sender)."""
        with _conn(self._path) as c:
            c.execute(
                "UPDATE users SET balance = balance - ? WHERE mobile = ?",
                (float(amount), mobile),
            )

    def add_daily_sent(self, mobile: str, amount: float):
        """Increment daily_sent counter."""
        with _conn(self._path) as c:
            c.execute(
                "UPDATE users SET daily_sent = daily_sent + ? WHERE mobile = ?",
                (float(amount), mobile),
            )

    # ── LINKED BANKS ──────────────────────────────────────────────────────────

    def get_linked_banks(self, mobile: str) -> list:
        with _conn(self._path) as c:
            rows = c.execute(
                "SELECT * FROM linked_banks WHERE mobile = ?", (mobile,)
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            extra = json.loads(d.pop("extra_json", "{}"))
            d.pop("id", None)
            d.update(extra)
            result.append(d)
        return result

    def add_linked_bank(self, mobile: str, entry: dict) -> bool:
        """Insert a linked bank. Returns False if account_number already linked."""
        with _conn(self._path) as c:
            exists = c.execute(
                "SELECT 1 FROM linked_banks WHERE mobile = ? AND account_number = ?",
                (mobile, entry["account_number"]),
            ).fetchone()
            if exists:
                return False
            core_keys = {"bank_code", "bank_name", "account_number", "account_title", "linked_at"}
            extra = {k: v for k, v in entry.items() if k not in core_keys}
            c.execute(
                """INSERT INTO linked_banks
                   (mobile, bank_code, bank_name, account_number, account_title, linked_at, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    mobile,
                    entry.get("bank_code", ""),
                    entry.get("bank_name", ""),
                    entry.get("account_number", ""),
                    entry.get("account_title", ""),
                    entry.get("linked_at", ""),
                    json.dumps(extra),
                ),
            )
        return True

    # ── DEV TOOLS ─────────────────────────────────────────────────────────────

    def reset_all(self):
        """Wipe all data and re-seed from the original seed dict."""
        with _conn(self._path) as c:
            c.execute("DELETE FROM users")
            c.execute("DELETE FROM linked_banks")
            self._seed_all(c)

    def reset_daily_sent(self):
        """Reset daily_sent to 0 for all users (e.g. simulate midnight reset)."""
        with _conn(self._path) as c:
            c.execute("UPDATE users SET daily_sent = 0")


# ─────────────────────────────────────────────────────────────────────────────
# BankAccountDB — for 1LINK / IBFT
# ─────────────────────────────────────────────────────────────────────────────

class BankAccountDB:
    """SQLite-backed IBFT bank account store (for 1LINK)."""

    def __init__(self, db_path: str, seed_data: dict):
        self._path = db_path
        self._seed = seed_data
        self._init()

    def _init(self):
        with _conn(self._path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS bank_accounts (
                    account_key    TEXT PRIMARY KEY,
                    bank_code      TEXT NOT NULL,
                    bank_name      TEXT NOT NULL,
                    account_number TEXT NOT NULL,
                    account_title  TEXT NOT NULL,
                    balance        REAL NOT NULL DEFAULT 0,
                    status         TEXT NOT NULL DEFAULT 'active',
                    extra_json     TEXT NOT NULL DEFAULT '{}'
                )
            """)
            if c.execute("SELECT COUNT(*) FROM bank_accounts").fetchone()[0] == 0:
                self._seed_all(c)

    def _seed_all(self, c):
        for key, data in self._seed.items():
            extra = {k: v for k, v in data.items() if k not in _BANK_CORE}
            c.execute(
                """INSERT OR IGNORE INTO bank_accounts
                   (account_key, bank_code, bank_name, account_number,
                    account_title, balance, status, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key,
                    data.get("bank_code", ""),
                    data.get("bank_name", ""),
                    data.get("account_number", ""),
                    data.get("account_title", ""),
                    float(data.get("balance", 0)),
                    data.get("status", "active"),
                    json.dumps(extra),
                ),
            )

    def _row_to_dict(self, row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        extra = json.loads(d.pop("extra_json", "{}"))
        d.update(extra)
        return d

    # ── READ ──────────────────────────────────────────────────────────────────

    def get(self, bank_code: str, account_number: str) -> dict | None:
        with _conn(self._path) as c:
            row = c.execute(
                "SELECT * FROM bank_accounts WHERE bank_code = ? AND account_number = ?",
                (bank_code.upper(), account_number),
            ).fetchone()
        return self._row_to_dict(row)

    def all_balances(self) -> list:
        with _conn(self._path) as c:
            rows = c.execute(
                "SELECT bank_code, account_number, account_title, balance FROM bank_accounts"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── WRITE ─────────────────────────────────────────────────────────────────

    def credit(self, bank_code: str, account_number: str, amount: float):
        with _conn(self._path) as c:
            c.execute(
                """UPDATE bank_accounts SET balance = balance + ?
                   WHERE bank_code = ? AND account_number = ?""",
                (float(amount), bank_code.upper(), account_number),
            )

    # ── DEV TOOLS ─────────────────────────────────────────────────────────────

    def reset_all(self):
        """Wipe and re-seed from original seed data."""
        with _conn(self._path) as c:
            c.execute("DELETE FROM bank_accounts")
            self._seed_all(c)
