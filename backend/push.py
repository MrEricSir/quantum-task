"""
Web Push helpers: VAPID key management and notification delivery.
"""
import json
import logging

import base64

try:
    from py_vapid import Vapid
    from pywebpush import webpush, WebPushException
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption,
    )
    _PUSH_AVAILABLE = True
except ImportError:
    _PUSH_AVAILABLE = False

from sqlalchemy.orm import Session

import models

log = logging.getLogger(__name__)

VAPID_CLAIMS_SUB = "mailto:admin@quantumtask.app"


def _pub_to_b64(vapid: "Vapid") -> str:
    raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _priv_to_pem(vapid: "Vapid") -> str:
    return vapid.private_key.private_bytes(
        Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
    ).decode()


def ensure_vapid_keys(db: Session) -> None:
    """Generate and persist VAPID keys on first call. No-op if already present or pywebpush missing."""
    if not _PUSH_AVAILABLE:
        return

    priv_row = db.query(models.AppSetting).filter_by(key="vapid_private_key").first()
    pub_row  = db.query(models.AppSetting).filter_by(key="vapid_public_key").first()
    if priv_row and pub_row and priv_row.value and pub_row.value:
        return

    v = Vapid()
    v.generate_keys()
    private_pem = _priv_to_pem(v)
    public_b64  = _pub_to_b64(v)

    for key, value in [("vapid_private_key", private_pem), ("vapid_public_key", public_b64)]:
        row = db.query(models.AppSetting).filter_by(key=key).first()
        if row:
            row.value = value
        else:
            db.add(models.AppSetting(key=key, value=value))
    db.commit()


def get_vapid_public_key(db: Session) -> str | None:
    row = db.query(models.AppSetting).filter_by(key="vapid_public_key").first()
    return row.value if row and row.value else None


def send_notification(subscription: models.PushSubscription, payload: dict, vapid_pem: str) -> bool:
    """Send a push notification. Returns False if the subscription is gone (should be deleted)."""
    if not _PUSH_AVAILABLE:
        return True
    try:
        vapid = Vapid.from_pem(vapid_pem.encode())
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"auth": subscription.keys_auth, "p256dh": subscription.keys_p256dh},
            },
            data=json.dumps(payload),
            vapid_private_key=vapid,
            vapid_claims={"sub": VAPID_CLAIMS_SUB},
        )
        return True
    except WebPushException as e:
        status = getattr(e.response, "status_code", None) if e.response else None
        if status in (404, 410):
            return False  # expired/unregistered — caller should delete
        log.warning("Push failed (endpoint=%s): %s", subscription.endpoint[:40], e)
        return True
    except Exception as e:
        log.warning("Push unexpected error: %s", e)
        return True
