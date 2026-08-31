import os
import logging
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException

httpx = None
try:
    import httpx
except ImportError:
    pass

import gateway_db
import email_engine
from dependencies import (
    get_current_user,
    ConfigUpdate,
    ParseEmailRequest
)
from services.sync_service import (
    perform_transaction_check,
    send_telegram_notification
)

logger = logging.getLogger("mbbank-webhook.admin")

router = APIRouter(tags=["Admin"])

@router.post("/admin/config")
async def admin_config_post(cfg: ConfigUpdate, authenticated: bool = Depends(get_current_user)):
    try:
        gateway_db.set_config("mb_bank_code", cfg.mb_bank_code or "MB")
        gateway_db.set_config("mb_account_number", cfg.mb_account_number or "")
        gateway_db.set_config("mb_account_name", cfg.mb_account_name or "")
        gateway_db.set_config("default_callback_url", cfg.default_callback_url or "")
        gateway_db.set_config("email_auth_method", cfg.email_auth_method or "oauth2")
        if cfg.email_sender_filter is not None:
            gateway_db.set_config("email_sender_filter", cfg.email_sender_filter)
        if cfg.gmail_pubsub_label_ids is not None:
            gateway_db.set_config("gmail_pubsub_label_ids", cfg.gmail_pubsub_label_ids)
        if cfg.gmail_pubsub_topic is not None:
            gateway_db.set_config("gmail_pubsub_topic", cfg.gmail_pubsub_topic)
        if cfg.gmail_address:
            gateway_db.set_config("gmail_address", cfg.gmail_address)
        if cfg.gmail_app_password:
            gateway_db.set_config("gmail_app_password", cfg.gmail_app_password)
        if cfg.gmail_refresh_token:
            gateway_db.set_config("gmail_refresh_token", cfg.gmail_refresh_token)
        if cfg.telegram_bot_token is not None:
            gateway_db.set_config("telegram_bot_token", cfg.telegram_bot_token)
        if cfg.telegram_chat_id is not None:
            gateway_db.set_config("telegram_chat_id", cfg.telegram_chat_id)
        if cfg.telegram_notify_active is not None:
            gateway_db.set_config("telegram_notify_active", cfg.telegram_notify_active)
        return {"success": True, "message": "Đã lưu cấu hình tài khoản ngân hàng, Gmail & Telegram Bot thành công!"}
    except Exception as e:
        logger.error(f"Error saving admin configs: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/test-email-connection")
async def test_email_connection(authenticated: bool = Depends(get_current_user)):
    """Tests connection to Gmail (IMAP or OAuth2) and parses recent emails."""
    auth_method = gateway_db.get_config("email_auth_method", "imap")
    sender_filter = gateway_db.get_config("email_sender_filter", "")
    
    try:
        if auth_method == "oauth2":
            client_id = gateway_db.get_config("gmail_client_id")
            client_secret = gateway_db.get_config("gmail_client_secret")
            refresh_token = gateway_db.get_config("gmail_refresh_token")
            label_filter = gateway_db.get_config("gmail_pubsub_label_ids", "BankNotify")
            emails = await email_engine.fetch_emails_via_oauth2(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                sender_filter=sender_filter,
                max_emails=5,
                label_filter=label_filter
            )
        else:
            gmail_address = gateway_db.get_config("gmail_address")
            app_password = gateway_db.get_config("gmail_app_password")
            emails = await asyncio.to_thread(
                email_engine.fetch_emails_via_imap,
                gmail_address=gmail_address,
                app_password=app_password,
                sender_filter=sender_filter,
                max_emails=5
            )
            
        parsed_samples = []
        r_amt = gateway_db.get_config("email_parser_regex_amount")
        r_cnt = gateway_db.get_config("email_parser_regex_content")
        r_trn = gateway_db.get_config("email_parser_regex_trans_no")
        r_dat = gateway_db.get_config("email_parser_regex_date")

        for em in emails:
            parsed = email_engine.parse_bank_email_html(
                html_content=em["html"],
                regex_amount=r_amt,
                regex_content=r_cnt,
                regex_trans_no=r_trn,
                regex_date=r_dat
            )
            parsed_samples.append({
                "subject": em["subject"],
                "from": em["from"],
                "date": em["date"],
                "parsed": parsed
            })

        return {
            "success": True,
            "count": len(emails),
            "samples": parsed_samples,
            "message": f"Kết nối Gmail ({auth_method.upper()}) thành công! Đã đọc thử {len(emails)} email."
        }
    except Exception as e:
        logger.error(f"Test email connection error: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/parse-email-sample")
async def parse_email_sample(req: ParseEmailRequest, authenticated: bool = Depends(get_current_user)):
    """Parses raw HTML email content against provided or saved regex rules."""
    try:
        res = email_engine.parse_bank_email_html(
            html_content=req.html_content,
            regex_amount=req.regex_amount,
            regex_content=req.regex_content,
            regex_trans_no=req.regex_trans_no,
            regex_date=req.regex_date
        )
        return {
            "success": True,
            "parsed": res
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/scan-now")
async def scan_now_endpoint(authenticated: bool = Depends(get_current_user)):
    """Forces an immediate manual scan of recent transactions for match check."""
    try:
        count = await perform_transaction_check()
        return {"success": True, "processed_count": count}
    except Exception as e:
        logger.error(f"Manual scan failed: {e}")
        return {"success": False, "error": str(e)}

@router.delete("/api/pending-payments/{payment_id}")
async def delete_pending_payment_endpoint(payment_id: str, authenticated: bool = Depends(get_current_user)):
    """Deletes a pending payment from the queue."""
    try:
        gateway_db.delete_pending_payment(payment_id)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting pending payment {payment_id}: {e}")
        return {"success": False, "error": str(e)}

@router.delete("/api/pending-payments")
async def delete_all_pending_payments_endpoint(authenticated: bool = Depends(get_current_user)):
    """Deletes all pending payments from the queue."""
    try:
        gateway_db.delete_all_pending_payments()
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting all pending payments: {e}")
        return {"success": False, "error": str(e)}

@router.post("/api/test-telegram")
async def test_telegram_endpoint(authenticated: bool = Depends(get_current_user)):
    """Sends a test notification to the configured Telegram Chat ID."""
    bot_token = gateway_db.get_config("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = gateway_db.get_config("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
    
    if not bot_token:
        return {"success": False, "error": "Chưa cấu hình Telegram Bot Token trong file .env hoặc Admin!"}
    if not chat_id:
        return {"success": False, "error": "Vui lòng nhập Telegram Chat ID của bạn trên Admin Dashboard!"}
        
    test_msg = (
        f"🤖 <b>[KOS GATEWAY] TEST THÔNG BÁO TELEGRAM</b>\n"
        f"---------------------------------\n"
        f"✅ Bot Telegram đã kết nối thành công với KOS Gateway!\n"
        f"⏰ <b>Thời gian test:</b> {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}\n"
        f"📩 <b>Chat ID nhận thông báo:</b> <code>{chat_id}</code>"
    )
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": test_msg,
        "parse_mode": "HTML"
    }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                return {"success": True, "message": f"Đã gửi tin nhắn test thành công tới Telegram Chat ID {chat_id}!"}
            else:
                return {"success": False, "error": f"Lỗi từ Telegram API (HTTP {res.status_code}): {res.text}"}
    except Exception as e:
        return {"success": False, "error": f"Lỗi kết nối Telegram: {str(e)}"}

@router.post("/api/admin/gmail-watch/subscribe")
async def subscribe_gmail_watch_endpoint(
    topic_name: str, 
    label_ids: Optional[str] = "BankNotify", 
    authenticated: bool = Depends(get_current_user)
):
    """
    Enables Google Cloud Pub/Sub Push Watch for Gmail inbox or custom label.
    Requires topic_name in format: projects/YOUR_PROJECT/topics/YOUR_TOPIC
    label_ids can be 'BankNotify', 'INBOX', etc.
    """
    c_id = gateway_db.get_config("gmail_client_id")
    c_sec = gateway_db.get_config("gmail_client_secret")
    r_tok = gateway_db.get_config("gmail_refresh_token")
    if not c_id or not c_sec or not r_tok:
        raise HTTPException(status_code=400, detail="Chưa cấu hình Google OAuth2 Client ID/Secret/Refresh Token.")

    labels_list = [l.strip() for l in label_ids.split(",") if l.strip()] if label_ids else ["BankNotify"]

    try:
        res = await email_engine.subscribe_gmail_watch(
            client_id=c_id,
            client_secret=c_sec,
            refresh_token=r_tok,
            topic_name=topic_name,
            label_ids=labels_list
        )
        gateway_db.set_config("gmail_pubsub_topic", topic_name)
        gateway_db.set_config("gmail_pubsub_label_ids", ",".join(labels_list))
        if res.get("expiration"):
            exp_val = str(res.get("expiration"))
            gateway_db.set_config("gmail_watch_expiration", exp_val)
            exp_dt = datetime.fromtimestamp(int(exp_val) / 1000.0).strftime('%Y-%m-%d %H:%M:%S')
        else:
            exp_dt = "Không xác định"

        tele_msg = (
            f"🔔 <b>[KOS GATEWAY] KÍCH HOẠT GMAIL PUSH WATCH SUCCESS</b>\n"
            f"---------------------------------\n"
            f"✅ <b>Trạng thái:</b> Đã kết nối Pub/Sub Push thành công!\n"
            f"📢 <b>Topic:</b> <code>{topic_name}</code>\n"
            f"🏷️ <b>Label:</b> <code>{','.join(labels_list)}</code>\n"
            f"⏱️ <b>Thời điểm hết hạn:</b> {exp_dt} UTC"
        )
        asyncio.create_task(send_telegram_notification(tele_msg))

        return {"success": True, "data": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
