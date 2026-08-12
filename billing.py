"""
Lotwise — Paddle Billing Integration

Paddle acts as merchant of record: it hosts checkout, charges the customer's
card, handles recurring billing, and pays out to your connected bank/Wise
account. This module wires Paddle's side of that into the app.

SETUP NEEDED (you provide these — see README):
  1. Create a Paddle account at paddle.com, verify your business.
  2. In Paddle's dashboard, create 6 Products/Prices (one per tier per side):
     supplier_standard, supplier_plus, supplier_premium,
     buyer_standard, buyer_plus, buyer_premium
  3. Copy each Price ID into PADDLE_PRICE_IDS below (or set as env vars).
  4. Get your Paddle API key + webhook signing secret from Developer Tools
     in the Paddle dashboard, set as env vars PADDLE_API_KEY and
     PADDLE_WEBHOOK_SECRET.
  5. Point a webhook at https://yourdomain.com/billing/webhook in Paddle's
     dashboard (Notifications settings), subscribing to subscription.*
     and transaction.* events.
  6. Connect Paddle's payout to your Wise Business account under
     Paddle's payout settings (Payoneer or wire transfer, per Paddle's docs).
"""
import os
import hmac
import hashlib
import json

PADDLE_API_KEY = os.environ.get("PADDLE_API_KEY", "")
PADDLE_WEBHOOK_SECRET = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
PADDLE_ENV = os.environ.get("PADDLE_ENV", "sandbox")  # "sandbox" or "production"

PADDLE_CLIENT_TOKEN = os.environ.get("PADDLE_CLIENT_TOKEN", "")  # public, used in checkout.js on the frontend

# Map internal tier/account_type combos to Paddle Price IDs.
# Fill these in from your Paddle dashboard once products are created.
PADDLE_PRICE_IDS = {
    ("supplier", "standard"): os.environ.get("PADDLE_PRICE_SUPPLIER_STANDARD", ""),
    ("supplier", "plus"):     os.environ.get("PADDLE_PRICE_SUPPLIER_PLUS", ""),
    ("supplier", "premium"):  os.environ.get("PADDLE_PRICE_SUPPLIER_PREMIUM", ""),
    ("buyer", "standard"):    os.environ.get("PADDLE_PRICE_BUYER_STANDARD", ""),
    ("buyer", "plus"):        os.environ.get("PADDLE_PRICE_BUYER_PLUS", ""),
    ("buyer", "premium"):     os.environ.get("PADDLE_PRICE_BUYER_PREMIUM", ""),
}


def get_price_id(account_type: str, tier: str) -> str:
    return PADDLE_PRICE_IDS.get((account_type, tier), "")


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Paddle signs webhooks as: ts=<timestamp>;h1=<hmac>
    We recompute the HMAC over "<timestamp>:<raw_body>" using the webhook
    secret and compare. Returns False (reject) if the secret isn't configured
    yet, so nothing is silently trusted before setup is complete.
    """
    if not PADDLE_WEBHOOK_SECRET or not signature_header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(";"))
        ts, h1 = parts.get("ts"), parts.get("h1")
        if not ts or not h1:
            return False
        signed_payload = f"{ts}:{raw_body.decode('utf-8')}"
        computed = hmac.new(
            PADDLE_WEBHOOK_SECRET.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed, h1)
    except Exception:
        return False


def parse_webhook_event(raw_body: bytes) -> dict:
    return json.loads(raw_body)


def is_paddle_configured() -> bool:
    return bool(PADDLE_API_KEY and PADDLE_CLIENT_TOKEN)
