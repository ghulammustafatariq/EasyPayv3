"""
app/services/payment_network_service.py — EasyPay v3.0

Integration layer that calls the 6 external mock payment network servers.
Each function normalises the external response into a consistent dict so the
API layer never has to parse raw network formats.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_lib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import Settings

settings = Settings()
logger = logging.getLogger("easypay")

# ── External network base URLs (override via env vars for production) ──────────
NETWORK_URLS: dict[str, str] = {
    "ONELINK":   getattr(settings, "ONELINK_URL",   "http://localhost:9000"),
    "JAZZCASH":  getattr(settings, "JAZZCASH_URL",  "http://localhost:9001"),
    "EASYPAISA": getattr(settings, "EASYPAISA_URL", "http://localhost:9002"),
    "NAYAPAY":   getattr(settings, "NAYAPAY_URL",   "http://localhost:9003"),
    "UPAY":      getattr(settings, "UPAY_URL",      "http://localhost:9004"),
    "SADAPAY":   getattr(settings, "SADAPAY_URL",   "http://localhost:9005"),
    "BILLS":     getattr(settings, "BILLS_URL",     "http://localhost:9006"),
}

TIMEOUT = httpx.Timeout(15.0)

# Networks that support wallet-to-wallet transfers
WALLET_NETWORKS = {"JAZZCASH", "EASYPAISA", "NAYAPAY", "UPAY", "SADAPAY"}


class ExternalNetworkError(Exception):
    """Raised when a downstream payment network returns a failure response."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── LOW-LEVEL HTTP HELPER ─────────────────────────────────────────────────────

async def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST JSON to an external network. Raises ExternalNetworkError on failure."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        raise ExternalNetworkError(
            code="NETWORK_TIMEOUT",
            message="External payment network did not respond in time. Please try again.",
        )
    except httpx.HTTPStatusError as exc:
        raise ExternalNetworkError(
            code="NETWORK_HTTP_ERROR",
            message=f"External network returned HTTP {exc.response.status_code}.",
        )
    except Exception as exc:
        raise ExternalNetworkError(
            code="NETWORK_UNAVAILABLE",
            message=f"Could not reach external payment network: {exc}",
        )


async def _get(url: str) -> dict[str, Any]:
    """GET from an external URL. Raises ExternalNetworkError on failure."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        raise ExternalNetworkError(
            code="NETWORK_TIMEOUT",
            message="External payment network timed out.",
        )
    except Exception as exc:
        raise ExternalNetworkError(
            code="NETWORK_UNAVAILABLE",
            message=f"Could not reach external payment network: {exc}",
        )


# ── WALLET TRANSFERS ──────────────────────────────────────────────────────────

async def lookup_wallet_account(network: str, mobile_number: str) -> dict[str, Any]:
    """
    Verify a wallet account on the target network before sending.
    Returns: {"account_name": str, "mobile_number": str}
    """
    network = network.upper()
    if network not in WALLET_NETWORKS:
        raise ExternalNetworkError(
            code="UNSUPPORTED_NETWORK",
            message=f"'{network}' is not a supported wallet network. "
                    f"Supported: {', '.join(sorted(WALLET_NETWORKS))}",
        )

    base = NETWORK_URLS[network]

    lookup_paths = {
        "JAZZCASH":  "/api/lookup",
        "EASYPAISA": "/MWALLET/account/lookup",
        "NAYAPAY":   "/v1/accounts/lookup",
        "UPAY":      "/api/v1/user/verify",
        "SADAPAY":   "/v2/wallets/lookup",
    }

    raw = await _post(f"{base}{lookup_paths[network]}", {"mobile_number": mobile_number})

    if not raw.get("success"):
        error_code = raw.get("error_code") or raw.get("error", {}).get("code", "LOOKUP_FAILED")
        message = (
            raw.get("message")
            or raw.get("pp_ResponseMessage")
            or raw.get("responseDesc")
            or raw.get("error", {}).get("description")
            or "Account lookup failed."
        )
        raise ExternalNetworkError(code=error_code, message=message)

    # Normalise account name across all network formats
    account_name = (
        raw.get("account_name")
        or raw.get("user_name")
        or raw.get("customerName")
        or (raw.get("data") or {}).get("display_name")
        or (raw.get("wallet") or {}).get("display_name")
        or "Account Holder"
    )

    return {"account_name": account_name, "mobile_number": mobile_number, "network": network}


async def send_to_wallet(
    network: str,
    sender_mobile: str,
    receiver_mobile: str,
    amount: float,
    transaction_ref: str,
    description: str = "",
) -> dict[str, Any]:
    """
    Send money from EasyPay to a wallet on the target network.
    Returns: {"transaction_id": str, "receiver_name": str, "amount": float}
    """
    network = network.upper()
    if network not in WALLET_NETWORKS:
        raise ExternalNetworkError(
            code="UNSUPPORTED_NETWORK",
            message=f"'{network}' is not a supported wallet network.",
        )

    base = NETWORK_URLS[network]

    send_paths = {
        "JAZZCASH":  "/api/send",
        "EASYPAISA": "/MWALLET/account/sendmoney",
        "NAYAPAY":   "/v1/transfers/send",
        "UPAY":      "/api/v1/transfer",
        "SADAPAY":   "/v2/transfers",
    }

    payload = {
        "sender_mobile": sender_mobile,
        "receiver_mobile": receiver_mobile,
        "amount": amount,
        "transaction_ref": transaction_ref,
        "description": description,
    }

    raw = await _post(f"{base}{send_paths[network]}", payload)

    if not raw.get("success"):
        error_code = raw.get("error_code") or raw.get("error", {}).get("code", "TRANSFER_FAILED")
        message = (
            raw.get("message")
            or raw.get("pp_ResponseMessage")
            or raw.get("responseDesc")
            or raw.get("error", {}).get("description")
            or "Transfer failed."
        )
        raise ExternalNetworkError(code=error_code, message=message)

    # Normalise transaction ID across formats
    txn_id = (
        raw.get("transaction_id")
        or raw.get("pp_TxnRefNo")
        or raw.get("transactionId")
        or (raw.get("data") or {}).get("transaction_id")
        or (raw.get("transfer") or {}).get("id")
        or transaction_ref
    )
    receiver_name = (
        raw.get("receiver_name")
        or raw.get("pp_ReceiverName")
        or raw.get("receiverName")
        or (raw.get("data") or {}).get("receiver_name")
        or (raw.get("transfer", {}).get("receiver") or {}).get("name")
        or "Recipient"
    )

    return {
        "transaction_id": txn_id,
        "receiver_name": receiver_name,
        "amount": amount,
        "network": network,
    }


# ── IBFT / BANK TRANSFERS ─────────────────────────────────────────────────────

async def lookup_bank_account(bank_code: str, account_number: str) -> dict[str, Any]:
    """Verify a bank account via 1LINK IBFT."""
    base = NETWORK_URLS["ONELINK"]
    raw = await _post(f"{base}/ibft/lookup", {
        "bank_code": bank_code.upper(),
        "account_number": account_number,
    })

    if not raw.get("success"):
        error_code = raw.get("error_code", "BANK_LOOKUP_FAILED")
        message = raw.get("message", "Bank account lookup failed.")
        raise ExternalNetworkError(code=error_code, message=message)

    return {
        "account_title": raw["account_title"],
        "bank_name": raw["bank_name"],
        "bank_code": raw["bank_code"],
        "account_number": account_number,
    }


async def send_bank_transfer(
    source_account: str,
    dest_bank_code: str,
    dest_account_number: str,
    amount: float,
    transaction_ref: str,
    remarks: str = "",
) -> dict[str, Any]:
    """Execute an IBFT bank transfer via 1LINK."""
    base = NETWORK_URLS["ONELINK"]
    raw = await _post(f"{base}/ibft/transfer", {
        "source_bank": "EASYPAY",
        "source_account": source_account,
        "dest_bank_code": dest_bank_code.upper(),
        "dest_account_number": dest_account_number,
        "amount": amount,
        "transaction_ref": transaction_ref,
        "remarks": remarks,
    })

    if not raw.get("success"):
        error_code = raw.get("error_code", "IBFT_FAILED")
        message = raw.get("message", "Bank transfer failed.")
        raise ExternalNetworkError(code=error_code, message=message)

    return {
        "stan": raw["stan"],
        "rrn": raw["rrn"],
        "dest_account_title": raw["dest_account_title"],
        "amount": amount,
        "status": raw["status"],
    }


# ── BILL PAYMENTS (via 1LINK BPSP) ───────────────────────────────────────────

async def fetch_bill(company: str, consumer_number: str) -> dict[str, Any]:
    """Fetch bill details before payment."""
    base = NETWORK_URLS["BILLS"]
    raw = await _post(f"{base}/inquiry", {
        "company": company.upper(),
        "consumer_number": consumer_number,
    })

    if not raw.get("success"):
        error_code = raw.get("error_code", "BILL_FETCH_FAILED")
        message = raw.get("message", "Could not retrieve bill details.")
        raise ExternalNetworkError(code=error_code, message=message)

    return {
        "company": raw["company"],
        "consumer_number": raw["consumer_number"],
        "consumer_name": raw.get("consumer_name", ""),
        "amount_due": raw["amount_due"],
        "due_date": raw["due_date"],
        "bill_month": raw.get("bill_month", ""),
        "status": raw["status"],
        "surcharge": raw.get("surcharge", 0.00),
        "total_payable": raw["total_payable"],
        "units_consumed": raw.get("units_consumed"),
        "address": raw.get("address", ""),
    }


async def pay_bill(
    company: str,
    consumer_number: str,
    amount: float,
    transaction_ref: str,
) -> dict[str, Any]:
    """Pay a utility bill via the dedicated Bills server."""
    base = NETWORK_URLS["BILLS"]
    raw = await _post(f"{base}/pay", {
        "company": company.upper(),
        "consumer_number": consumer_number,
        "amount": amount,
        "transaction_ref": transaction_ref,
    })

    if not raw.get("success"):
        error_code = raw.get("error_code", "BILL_PAYMENT_FAILED")
        message = raw.get("message", "Bill payment failed.")
        raise ExternalNetworkError(code=error_code, message=message)

    return {
        "bpsp_reference": raw.get("bpsp_reference") or raw.get("receipt_reference", ""),
        "consumer_name": raw.get("consumer_name", ""),
        "amount_paid": raw["amount_paid"],
        "paid_at": raw["paid_at"],
        "company": raw["company"],
    }


async def get_bill_companies() -> list[dict[str, Any]]:
    """Return the list of supported bill companies from the Bills server."""
    base = NETWORK_URLS["BILLS"]
    raw = await _get(f"{base}/companies")
    # Bills server returns {categories: [{category, companies: []}]}
    # Flatten to a list of {code, name, category} dicts for the API layer
    result: list[dict] = []
    for cat_group in raw.get("categories", []):
        for company in cat_group.get("companies", []):
            result.append({
                "code": company["code"],
                "name": company["name"],
                "category": cat_group["category"],
            })
    return result


# ── MOBILE WALLET TOP-UP (JazzCash / EasyPaisa → EasyPay) ────────────────────

_JAZZCASH_SANDBOX_URL = "https://sandbox.jazzcash.com.pk/ApplicationAPI/API/2.0/Purchase/DoMWalletTransaction"

MOBILE_TOPUP_NETWORKS = {"JAZZCASH", "EASYPAISA"}


def _jazzcash_secure_hash(params: dict[str, Any], hash_key: str) -> str:
    """Compute JazzCash HMAC-SHA256 secure hash over sorted param values."""
    sorted_values = [str(v) for _, v in sorted(params.items())]
    data = hash_key + "&" + "&".join(sorted_values)
    return hmac_lib.new(
        hash_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256
    ).hexdigest().upper()


async def _jazzcash_real_topup(mobile_number: str, amount: float, txn_ref: str) -> dict[str, Any]:
    """Call the real JazzCash sandbox MWALLET API to collect payment."""
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=1)

    params: dict[str, Any] = {
        "pp_Version": "1.1",
        "pp_TxnType": "MWALLET",
        "pp_Language": "EN",
        "pp_MerchantID": settings.JAZZCASH_MERCHANT_ID,
        "pp_SubMerchantID": "",
        "pp_Password": settings.JAZZCASH_PASSWORD,
        "pp_BankID": "TBANK",
        "pp_ProductID": "RETL",
        "pp_TxnRefNo": txn_ref,
        "pp_Amount": str(int(amount * 100)),
        "pp_TxnCurrency": "PKR",
        "pp_TxnDateTime": now.strftime("%Y%m%d%H%M%S"),
        "pp_TxnExpiryDateTime": expiry.strftime("%Y%m%d%H%M%S"),
        "pp_BillReference": "walletTopup",
        "pp_Description": "EasyPay wallet top-up via JazzCash",
        "pp_ReturnURL": "https://easypay.app/topup/callback",
        "pp_MobileNumber": mobile_number,
        "ppmpf_1": "", "ppmpf_2": "", "ppmpf_3": "", "ppmpf_4": "", "ppmpf_5": "",
    }
    params["pp_SecureHash"] = _jazzcash_secure_hash(
        {k: v for k, v in params.items()}, settings.JAZZCASH_HASH_KEY
    )

    url = settings.JAZZCASH_BASE_URL or _JAZZCASH_SANDBOX_URL
    raw = await _post(url, params)

    if raw.get("pp_ResponseCode") != "000":
        raise ExternalNetworkError(
            code=raw.get("pp_ResponseCode", "PAYMENT_FAILED"),
            message=raw.get("pp_ResponseMessage", "JazzCash payment failed"),
        )

    return {
        "success": True,
        "pp_TxnRefNo": raw.get("pp_TxnRefNo", txn_ref),
        "pp_ResponseCode": raw["pp_ResponseCode"],
        "pp_ResponseMessage": raw.get("pp_ResponseMessage", "Transaction Successful"),
    }


async def collect_mobile_wallet_topup(
    network: str,
    mobile_number: str,
    amount: float,
    transaction_ref: str,
) -> dict[str, Any]:
    """
    Collect a payment from a customer's JazzCash or EasyPaisa mobile wallet.
    Uses the real sandbox API if credentials are set in env, else falls back to
    the local mock server.
    Returns: {"transaction_id": str, "network": str}
    """
    network = network.upper()
    if network not in MOBILE_TOPUP_NETWORKS:
        raise ExternalNetworkError(
            code="UNSUPPORTED_NETWORK",
            message=f"'{network}' is not supported for mobile wallet top-up. "
                    f"Use JAZZCASH or EASYPAISA.",
        )

    if network == "JAZZCASH":
        use_real = bool(settings.JAZZCASH_MERCHANT_ID and settings.JAZZCASH_HASH_KEY)
        if use_real:
            data = await _jazzcash_real_topup(mobile_number, amount, transaction_ref)
            return {
                "transaction_id": data.get("pp_TxnRefNo", transaction_ref),
                "network": network,
                "provider_ref": data.get("pp_TxnRefNo", transaction_ref),
            }
        else:
            url = f"{NETWORK_URLS['JAZZCASH']}/api/topup"
            raw = await _post(url, {
                "mobile_number": mobile_number,
                "amount": amount,
                "transaction_ref": transaction_ref,
            })
            if not raw.get("success"):
                raise ExternalNetworkError(
                    code=raw.get("error_code", "PAYMENT_FAILED"),
                    message=raw.get("pp_ResponseMessage", "JazzCash payment failed"),
                )
            return {
                "transaction_id": raw.get("pp_TxnRefNo", transaction_ref),
                "network": network,
                "provider_ref": raw.get("pp_TxnRefNo", transaction_ref),
            }

    else:  # EASYPAISA
        use_real = bool(settings.EASYPAISA_STORE_ID and settings.EASYPAISA_HASH_KEY)
        if use_real:
            # Real EasyPaisa sandbox integration can be wired here when credentials are available
            raise ExternalNetworkError(
                code="NOT_CONFIGURED",
                message="EasyPaisa real sandbox credentials not yet configured.",
            )
        else:
            url = f"{NETWORK_URLS['EASYPAISA']}/MWALLET/account/topup"
            raw = await _post(url, {
                "mobile_number": mobile_number,
                "amount": amount,
                "transaction_ref": transaction_ref,
            })
            if not raw.get("success"):
                raise ExternalNetworkError(
                    code=raw.get("error_code", "PAYMENT_FAILED"),
                    message=raw.get("responseDesc", "EasyPaisa payment failed"),
                )
            return {
                "transaction_id": raw.get("transactionId", transaction_ref),
                "network": network,
                "provider_ref": raw.get("orderRefNum", transaction_ref),
            }
