"""
Lotwise — NOWPayments (crypto) Billing Integration

NOWPayments hosts a checkout page: we create an "invoice" via their API,
send the user to the hosted invoice_url to pay in crypto, and they call
our webhook (IPN) when the payment status changes. We verify the IPN
signature before trusting it.

Env vars used (set these in Render):
  NOWPAYMENTS_API_KEY   — from NOWPayments dashboard > Settings > API keys
  NOWPAYMENTS_IPN_SECRET — from the same page, "Generate" the IPN key
"""
import os
import json
import hmac
import hashlib
import requests

NOWPAYMENTS_API_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_IPN_SECRET = os.environ.get("NOWPAYMENTS_IPN_SECRET", "")
NOWPAYMENTS_API_BASE = "https://api.nowpayments.io/v1"


def is_nowpayments_configured() -> bool:
    return bool(NOWPAYMENTS_API_KEY)


def create_invoice(amount_usd, order_id, order_description, success_url, cancel_url, ipn_callback_url):
    """
    Creates a hosted NOWPayments invoice. Returns the dict response (contains
    'invoice_url' to redirect the user to) or raises on failure.
    """
    resp = requests.post(
        f"{NOWPAYMENTS_API_BASE}/invoice",
        headers={
            "x-api-key": NOWPAYMENTS_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "price_amount": amount_usd,
            "price_currency": "usd",
            "order_id": order_id,
            "order_description": order_description,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "ipn_callback_url": ipn_callback_url,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _sorted_json(data: dict) -> str:
    """NOWPayments signs the IPN body as JSON with keys sorted alphabetically
    and no extra whitespace — must match exactly or the signature won't verify."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def verify_ipn_signature(parsed_body: dict, signature_header: str) -> bool:
    """
    NOWPayments sends header 'x-nowpayments-sig' = HMAC-SHA512 of the
    sorted-key JSON body, using the IPN secret. Returns False (reject) if
    the secret isn't configured yet, so nothing is silently trusted.
    """
    if not NOWPAYMENTS_IPN_SECRET or not signature_header:
        return False
    try:
        payload = _sorted_json(parsed_body)
        computed = hmac.new(
            NOWPAYMENTS_IPN_SECRET.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(computed, signature_header)
    except Exception:
        return False
