import os
import logging
import asyncio
import base64
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, status

import gateway_db
from services.sync_service import (
    perform_transaction_check,
    ensure_gmail_watch_active
)

logger = logging.getLogger("mbbank-webhook.webhooks")

router = APIRouter(tags=["Webhooks"])

@router.post("/api/webhooks/gmail-push")
async def gmail_push_webhook(request: Request):
    """
    Webhook endpoint to receive Google Cloud Pub/Sub push notifications for incoming Gmail emails.
    Triggers immediate bank email scan and sends callback push webhook to the merchant.
    """
    try:
        body = await request.json()
        message = body.get("message", {})
        data_b64 = message.get("data")
        if data_b64:
            padded_b64 = data_b64 + "=" * (-len(data_b64) % 4)
            try:
                decoded_bytes = base64.urlsafe_b64decode(padded_b64.encode("ascii"))
            except Exception:
                decoded_bytes = base64.b64decode(padded_b64.encode("ascii"))
            decoded_data = decoded_bytes.decode("utf-8", errors="ignore")
            logger.info(f"Received Gmail Pub/Sub Push notification: {decoded_data}")

        # Run transaction scan synchronously to fetch email content and process matching
        count = await perform_transaction_check(force=True)
        return {"success": True, "message": "Gmail Push notification received", "processed_count": count}
    except Exception as e:
        logger.error(f"Error handling Gmail Push notification: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

@router.get("/api/cron")
async def cron_trigger(secret: Optional[str] = None, request: Request = None):
    """Secure endpoint for Vercel Cron or other automated pollers to trigger check."""
    is_vercel_cron = (request.headers.get("x-vercel-cron") == "1") if request else False
    expected_secret = os.environ.get("CRON_SECRET") or gateway_db.get_config("callback_secret")
    auth_header = request.headers.get("Authorization") if request else None
    header_token = None
    if auth_header and auth_header.startswith("Bearer "):
        header_token = auth_header.split(" ")[1]
        
    if not is_vercel_cron and secret != expected_secret and header_token != expected_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid secret token"
        )
        
    try:
        asyncio.create_task(ensure_gmail_watch_active())
        count = await perform_transaction_check()
        return {"success": True, "processed_count": count}

    except Exception as e:
        logger.error(f"Cron scan failed: {e}")
        return {"success": False, "error": str(e)}
