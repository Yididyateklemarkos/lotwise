"""
Lotwise — Whop Billing Integration (Whop API v1)

Card / Apple Pay / local payment methods, same shape as the NOWPayments
crypto flow: create a checkout configuration with an inline one-time plan,
send the user to Whop's hosted checkout (purchase_url), then activate the
subscription when the signed `payment.succeeded` webhook arrives.

Our internal order_id is passed as checkout metadata, and Whop copies that
metadata onto the payment — that's how a webhook maps back to a
PaymentOrder row.

Env vars used (set these in Render):
  WHOP_API_KEY         — company API key (apik_...) from Dashboard > Developer
  WHOP_COMPANY_ID      — biz_... from Dashboard > Settings
  WHOP_WEBHOOK_SECRET  — signing secret of the webhook endpoint, needed to
                         verify incoming webhook signatures
"""
import base64
import binascii
import hashlib
import hmac
import os
import time

import requests

WHOP_API_KEY = os.environ.get("WHOP_API_KEY", "")
WHOP_COMPANY_ID = os.environ.get("WHOP_COMPANY_ID", "")
WHOP_WEBHOOK_SECRET = os.environ.get("WHOP_WEBHOOK_SECRET", "")

WHOP_API_BASE = "https://api.whop.com/api/v1"

# Reject webhooks whose timestamp is too far from now — replay protection,
# per the Standard Webhooks spec Whop follows.
WEBHOOK_TOLERANCE_SECONDS = 5 * 60


def is_whop_configured() -> bool:
    return bool(WHOP_API_KEY and WHOP_COMPANY_ID)


def create_checkout(amount_usd, order_id, redirect_url):
    """
    Creates a Whop checkout configuration with an inline one-time plan and
    returns the full response dict. `purchase_url` in that dict is Whop's
    hosted checkout page — send the user there.
    """
    payload = {
        "plan": {
            "company_id": WHOP_COMPANY_ID,
            "initial_price": float(amount_usd),
            "plan_type": "one_time",
            "currency": "usd",
        },
        "metadata": {"order_id": order_id},
    }
    # Whop rejects non-https redirect URLs, so on http:// dev hosts the
    # buyer just stays on Whop's confirmation page — the webhook still
    # activates the order.
    if redirect_url.startswith("https://"):
        payload["redirect_url"] = redirect_url

    resp = requests.post(
        f"{WHOP_API_BASE}/checkout_configurations",
        headers={
            "Authorization": f"Bearer {WHOP_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_purchase_url(checkout_response: dict) -> str:
    return checkout_response.get("purchase_url", "") or ""


def _secret_keys():
    """Candidate HMAC keys for the configured signing secret.

    Standard Webhooks keys are usually `whsec_<base64>`, but Whop documents
    handing the secret to the verifier base64-encoded as-is, which means the
    raw string is the key. Both readings are tried so a correctly-configured
    secret verifies either way; an unknown secret still fails.
    """
    keys = [WHOP_WEBHOOK_SECRET.encode("utf-8")]
    if WHOP_WEBHOOK_SECRET.startswith("whsec_"):
        rest = WHOP_WEBHOOK_SECRET[len("whsec_"):]
        keys.append(rest.encode("utf-8"))
        try:
            keys.append(base64.b64decode(rest, validate=True))
        except (binascii.Error, ValueError):
            pass
    return keys


def verify_webhook_signature(raw_body: bytes, headers) -> bool:
    """
    Verifies a Whop webhook using the Standard Webhooks scheme: HMAC-SHA256
    over "<webhook-id>.<webhook-timestamp>.<raw body>", compared against the
    base64 signatures in the `webhook-signature` header (space separated,
    each prefixed with its version, e.g. "v1,<sig>").

    Returns False if nothing is configured, so a payload is never trusted by
    default.
    """
    if not WHOP_WEBHOOK_SECRET:
        return False

    msg_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    signature_header = headers.get("webhook-signature", "")
    if not msg_id or not timestamp or not signature_header:
        return False

    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - sent_at) > WEBHOOK_TOLERANCE_SECONDS:
        return False

    signed_payload = b"%s.%s.%s" % (
        msg_id.encode("utf-8"),
        timestamp.encode("utf-8"),
        raw_body,
    )
    expected = [
        base64.b64encode(hmac.new(key, signed_payload, hashlib.sha256).digest()).decode("utf-8")
        for key in _secret_keys()
    ]

    for part in signature_header.split(" "):
        _, _, sent_signature = part.partition(",")
        if not sent_signature:
            continue
        for candidate in expected:
            if hmac.compare_digest(candidate, sent_signature):
                return True
    return False
