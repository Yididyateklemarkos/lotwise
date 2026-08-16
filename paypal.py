"""
Lotwise — PayPal Billing Integration (Orders API v2)

One-time payments, same pattern as the NOWPayments crypto flow: create an
order, send the user to PayPal's approval page, they approve, we capture
the payment server-side, then extend their subscription 30 days.

Env vars used (set these in Render):
  PAYPAL_CLIENT_ID
  PAYPAL_CLIENT_SECRET
  PAYPAL_ENV            — "sandbox" or "live" (defaults to sandbox if unset)
  PAYPAL_WEBHOOK_ID     — from the webhook you create in the PayPal dashboard,
                           needed to verify incoming webhook signatures
"""
import os
import requests

PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
PAYPAL_ENV = os.environ.get("PAYPAL_ENV", "sandbox")
PAYPAL_WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID", "")

PAYPAL_API_BASE = (
    "https://api-m.paypal.com" if PAYPAL_ENV == "live" else "https://api-m.sandbox.paypal.com"
)


def is_paypal_configured() -> bool:
    return bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)


def _get_access_token() -> str:
    """PayPal's REST API is OAuth2 client-credentials — every call needs a
    fresh-ish bearer token first."""
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def create_order(amount_usd, order_id, order_description, return_url, cancel_url):
    """
    Creates a PayPal order and returns the full response dict. The response
    includes a 'links' array — find the one with rel == 'approve' and send
    the user there. custom_id carries our internal order_id through so the
    webhook/capture step can match it back to a PaymentOrder row.
    """
    token = _get_access_token()
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v2/checkout/orders",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "intent": "CAPTURE",
            "purchase_units": [{
                "custom_id": order_id,
                "description": order_description,
                "amount": {
                    "currency_code": "USD",
                    "value": f"{amount_usd:.2f}",
                },
            }],
            "application_context": {
                "brand_name": "Lotwise",
                "return_url": return_url,
                "cancel_url": cancel_url,
                "user_action": "PAY_NOW",
            },
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_approval_url(order_response: dict) -> str:
    for link in order_response.get("links", []):
        if link.get("rel") == "approve":
            return link.get("href")
    return ""


def capture_order(paypal_order_id: str) -> dict:
    """Captures (actually charges) a previously-created, buyer-approved
    order. Returns the capture response — check status == 'COMPLETED'."""
    token = _get_access_token()
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v2/checkout/orders/{paypal_order_id}/capture",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def verify_webhook_signature(headers: dict, raw_body_parsed: dict) -> bool:
    """
    Uses PayPal's own verify-webhook-signature endpoint rather than manually
    validating certificates — this is PayPal's officially recommended
    approach. Returns False (reject) if not configured, so nothing is
    silently trusted.
    """
    if not PAYPAL_WEBHOOK_ID:
        return False
    try:
        token = _get_access_token()
        resp = requests.post(
            f"{PAYPAL_API_BASE}/v1/notifications/verify-webhook-signature",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "auth_algo": headers.get("Paypal-Auth-Algo", ""),
                "cert_url": headers.get("Paypal-Cert-Url", ""),
                "transmission_id": headers.get("Paypal-Transmission-Id", ""),
                "transmission_sig": headers.get("Paypal-Transmission-Sig", ""),
                "transmission_time": headers.get("Paypal-Transmission-Time", ""),
                "webhook_id": PAYPAL_WEBHOOK_ID,
                "webhook_event": raw_body_parsed,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("verification_status") == "SUCCESS"
    except Exception:
        return False
