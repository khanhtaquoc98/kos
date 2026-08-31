import os
import re
import logging
import asyncio
import hashlib
from datetime import datetime
from typing import Optional, Any

httpx = None
try:
    import httpx
except ImportError:
    pass

import gateway_db
import email_engine

from typing import Optional, Any, Dict, List

logger = logging.getLogger("mbbank-webhook.sync")

# In-memory SSE subscribers: ref_id -> list of asyncio.Queue
_sse_subscribers: Dict[str, List[asyncio.Queue]] = {}

def subscribe_sse(ref_id: str) -> asyncio.Queue:
    queue = asyncio.Queue()
    if ref_id not in _sse_subscribers:
        _sse_subscribers[ref_id] = []
    _sse_subscribers[ref_id].append(queue)
    return queue

def unsubscribe_sse(ref_id: str, queue: asyncio.Queue):
    if ref_id in _sse_subscribers:
        if queue in _sse_subscribers[ref_id]:
            _sse_subscribers[ref_id].remove(queue)
        if not _sse_subscribers[ref_id]:
            del _sse_subscribers[ref_id]

def broadcast_payment_status(ref_id: str, status: str, payload: Optional[dict] = None):
    p = payload or {"status": status, "reference_id": ref_id}
    if ref_id in _sse_subscribers:
        for q in list(_sse_subscribers[ref_id]):
            try:
                q.put_nowait(p)
            except Exception:
                pass

async def send_telegram_notification(text: str):
    """Sends HTML formatted log notification to configured Telegram Chat ID via Bot API."""
    bot_token = gateway_db.get_config("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = gateway_db.get_config("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
    notify_active = gateway_db.get_config("telegram_notify_active", "true")
    
    if not bot_token:
        logger.warning("Telegram notification skipped: telegram_bot_token is missing.")
        return
    if not chat_id:
        logger.warning("Telegram notification skipped: telegram_chat_id is missing.")
        return
    if str(notify_active).lower() == "false":
        logger.info("Telegram notification skipped: telegram_notify_active is disabled.")
        return
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                logger.info(f"Telegram notification sent to chat_id {chat_id}")
            else:
                logger.error(f"Failed to send Telegram message: status={res.status_code}, resp={res.text}")
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")

async def send_payment_webhook(payment: dict, status: str, transaction: Optional[Any] = None, reason: str = ""):
    """
    Sends a signed HTTP POST push webhook to the client server for both SUCCESS and FAILURE/CANCEL events.
    Also triggers Telegram notification to admin chat immediately.
    """
    secret = gateway_db.get_config("callback_secret", "super-secret-callback-token")
    
    target_url = payment.get("webhook_url") or payment.get("callback_url") or gateway_db.get_config("default_callback_url")
    if target_url and "/api/" not in target_url:
        from urllib.parse import urlparse
        parsed = urlparse(target_url)
        if parsed.scheme and parsed.netloc:
            target_url = f"{parsed.scheme}://{parsed.netloc}/api/payment/webhook"
    ref_id = payment.get("reference_id") or payment.get("id")
    p_id = payment.get("id")
    amount = float(payment.get("amount") or 0.0)
    
    # Broadcast SSE event immediately to any open UI checkout page listeners
    broadcast_payment_status(ref_id, status, {"status": status, "reference_id": ref_id, "amount": amount})
    
    trans_no = getattr(transaction, "refNo", "") if transaction else ""
    desc = getattr(transaction, "description", "") or getattr(transaction, "addDescription", "") if transaction else (reason or payment.get("content", ""))
    txn_date = getattr(transaction, "transactionDate", "") if transaction else ""

    is_success = (status == "completed")
    event_type = "payment.success" if is_success else "payment.failed"

    # 1. Send Telegram notification IMMEDIATELY so admin receives notification without waiting for callback_url
    bank_code = gateway_db.get_config("mb_bank_code", "MB")
    if is_success:
        tele_msg = (
            f"🎉 <b>[KOS GATEWAY] THANH TOÁN THÀNH CÔNG</b>\n"
            f"---------------------------------\n"
            f"💰 <b>Số tiền:</b> +{amount:,.0f} VNĐ\n"
            f"📝 <b>Nội dung:</b> {payment.get('content', '')}\n"
            f"🔖 <b>Mã đơn hàng:</b> <code>{ref_id}</code>\n"
            f"🏛️ <b>Ngân hàng:</b> {bank_code}\n"
            f"💳 <b>Mã giao dịch:</b> {trans_no or 'N/A'}\n"
            f"⏱️ <b>Thời gian:</b> {txn_date or 'Vừa xong'}"
        )
    else:
        tele_msg = (
            f"⚠️ <b>[KOS GATEWAY] ĐƠN HÀNG ĐÃ HỦY</b>\n"
            f"---------------------------------\n"
            f"🔖 <b>Mã đơn hàng:</b> <code>{ref_id}</code>\n"
            f"💰 <b>Số tiền:</b> {amount:,.0f} VNĐ\n"
            f"📝 <b>Nội dung:</b> {payment.get('content', '')}\n"
            f"🔴 <b>Trạng thái:</b> {status.upper()}"
        )
    
    # Await telegram notification directly
    try:
        await send_telegram_notification(tele_msg)
    except Exception as e:
        logger.error(f"Error triggering Telegram notification in send_payment_webhook: {e}")

    # 2. Build Webhook Payload & Send to Merchant Callback URL
    payload = {
        "event": event_type,
        "status": status,  # 'completed', 'cancelled', 'failed'
        "order_id": ref_id,
        "reference_id": ref_id,
        "payment_id": p_id,
        "amount": amount,
        "trans_no": trans_no,
        "description": desc,
        "date": txn_date,
        "timestamp": int(datetime.utcnow().timestamp())
    }
    
    # Generate signature for integrity and authenticity
    sign_str = f"{ref_id}{p_id}{amount}{trans_no}{secret}"
    signature = hashlib.sha256(sign_str.encode()).hexdigest()
    payload["signature"] = signature
    
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature
    }
    
    if target_url:
        logger.info(f"Sending push webhook [{event_type}] to {target_url} for order {ref_id}...")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(target_url, json=payload, headers=headers)
                if res.status_code in [200, 201]:
                    logger.info(f"Webhook delivered successfully to {target_url}. Status: {res.status_code}")
                else:
                    logger.error(f"Webhook delivery failed to {target_url}. Status: {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"Failed to connect to webhook URL {target_url}: {e}")

async def ensure_gmail_watch_active(force: bool = False):
    """
    Checks if Gmail Watch is close to expiration (< 48 hours remaining), expired or forced,
    and automatically renews it with Google Cloud Pub/Sub.
    """
    topic_name = gateway_db.get_config("gmail_pubsub_topic")
    if not topic_name:
        return

    exp_str = gateway_db.get_config("gmail_watch_expiration", "0")
    try:
        exp_ms = int(exp_str)
    except ValueError:
        exp_ms = 0

    now_ms = int(datetime.utcnow().timestamp() * 1000)
    # Renew if forced, expired or will expire within 48 hours
    if force or (exp_ms - now_ms < (48 * 3600 * 1000)):
        c_id = gateway_db.get_config("gmail_client_id")
        c_sec = gateway_db.get_config("gmail_client_secret")
        r_tok = gateway_db.get_config("gmail_refresh_token")
        label_cfg = gateway_db.get_config("gmail_pubsub_label_ids", "BankNotify")
        labels_list = [l.strip() for l in label_cfg.split(",") if l.strip()]
        if c_id and c_sec and r_tok:
            try:
                res = await email_engine.subscribe_gmail_watch(
                    client_id=c_id,
                    client_secret=c_sec,
                    refresh_token=r_tok,
                    topic_name=topic_name,
                    label_ids=labels_list
                )

                new_exp = res.get("expiration")
                if new_exp:
                    gateway_db.set_config("gmail_watch_expiration", str(new_exp))
                    exp_dt = datetime.fromtimestamp(int(new_exp) / 1000.0).strftime('%Y-%m-%d %H:%M:%S')
                    logger.info(f"Auto-renewed Gmail Watch successfully! New expiration: {new_exp}")
                    
                    tele_msg = (
                        f"🤖 <b>[KOS GATEWAY] GIA HẠN GMAIL WATCH PUSH</b>\n"
                        f"---------------------------------\n"
                        f"✅ <b>Trạng thái:</b> Tự động gia hạn thành công (+7 ngày)\n"
                        f"📢 <b>Pub/Sub Topic:</b> <code>{topic_name}</code>\n"
                        f"⏱️ <b>Thời điểm hết hạn mới:</b> {exp_dt} UTC"
                    )
                    asyncio.create_task(send_telegram_notification(tele_msg))
            except Exception as e:
                logger.error(f"Failed to auto-renew Gmail Watch: {e}")
                err_msg = (
                    f"🚨 <b>[KOS GATEWAY] LỖI GIA HẠN GMAIL WATCH PUSH</b>\n"
                    f"---------------------------------\n"
                    f"🔴 <b>Chi tiết lỗi:</b> {str(e)}\n"
                    f"📢 <b>Pub/Sub Topic:</b> <code>{topic_name}</code>"
                )
                asyncio.create_task(send_telegram_notification(err_msg))

async def perform_transaction_check(force: bool = False) -> int:
    """
    Core engine function.
    Fetches recent transactions from Email (Gmail API / IMAP),
    matches against pending_payments in gateway_db,
    and sends callback webhooks to registered endpoints for matched items.
    """
    # Ensure Gmail Watch PubSub is active & up-to-date with configured labels FIRST
    asyncio.create_task(ensure_gmail_watch_active())

    pending = [p for p in gateway_db.get_pending_payments() if isinstance(p, dict) and p.get("status") == "pending"]
    processed_count = 0

    # ---------------- 1. EMAIL BANK NOTIFICATION SCAN ----------------
    email_active = gateway_db.get_config("email_gateway_active", "true")
    if email_active == "true":
        try:
            auth_method = gateway_db.get_config("email_auth_method", "imap")
            sender_filter = gateway_db.get_config("email_sender_filter", "")
            label_filter = gateway_db.get_config("gmail_pubsub_label_ids", "BankNotify")
            emails = []
            if auth_method == "oauth2":
                c_id = gateway_db.get_config("gmail_client_id")
                c_sec = gateway_db.get_config("gmail_client_secret")
                r_tok = gateway_db.get_config("gmail_refresh_token")
                if c_id and c_sec and r_tok:
                    fetch_limit = 3 if force else 15
                    emails = await email_engine.fetch_emails_via_oauth2(
                        client_id=c_id,
                        client_secret=c_sec,
                        refresh_token=r_tok,
                        sender_filter=sender_filter,
                        max_emails=fetch_limit,
                        label_filter=label_filter
                    )

            else:
                g_addr = gateway_db.get_config("gmail_address")
                g_pass = gateway_db.get_config("gmail_app_password")
                if g_addr and g_pass:
                    emails = await asyncio.to_thread(
                        email_engine.fetch_emails_via_imap,
                        gmail_address=g_addr, app_password=g_pass, sender_filter=sender_filter, max_emails=15, label_filter=label_filter
                    )

            if emails:
                r_amt = gateway_db.get_config("email_parser_regex_amount")
                r_cnt = gateway_db.get_config("email_parser_regex_content")
                r_trn = gateway_db.get_config("email_parser_regex_trans_no")
                r_dat = gateway_db.get_config("email_parser_regex_date")

                for em in emails:
                    parsed = email_engine.parse_bank_email_html(
                        html_content=em.get("html", ""),
                        regex_amount=r_amt,
                        regex_content=r_cnt,
                        regex_trans_no=r_trn,
                        regex_date=r_dat
                    )
                    
                    credit_amount = float(parsed.get("amount") or 0.0)
                    email_content = (parsed.get("content") or "").upper().strip()
                    full_raw_text = (parsed.get("raw_text") or "").upper().strip()
                    trans_no = parsed.get("trans_no") or em.get("msg_id", "")
                    
                    if credit_amount <= 0:
                        continue

                    # Check if processed
                    if gateway_db.is_transaction_processed(trans_no):
                        continue

                    matched = False
                    for pay in pending:
                        if not isinstance(pay, dict) or pay.get('status') != 'pending':
                            continue
                        
                        pay_content = (pay.get('content') or "").upper().strip()
                        if not pay_content:
                            continue

                        # 1. Exact substring match
                        exact_matched = (pay_content in email_content) or (pay_content in full_raw_text)

                        # 2. Normalized alphanumeric match (ignores spaces, hyphens, prefixes)
                        clean_pay = re.sub(r'[^A-Z0-9]', '', pay_content)
                        clean_email_cnt = re.sub(r'[^A-Z0-9]', '', email_content)
                        clean_full_txt = re.sub(r'[^A-Z0-9]', '', full_raw_text)

                        clean_matched = False
                        if clean_pay:
                            clean_matched = (clean_pay in clean_email_cnt) or (clean_pay in clean_full_txt)

                        content_matched = exact_matched or clean_matched
                        
                        try:
                            pay_amount = float(pay.get('amount') or 0.0)
                        except (ValueError, TypeError):
                            pay_amount = 0.0

                        amount_matched = abs(pay_amount - credit_amount) < 1.0

                        if content_matched and amount_matched:
                            logger.info(f"EMAIL MATCH FOUND: Trans {trans_no} matches pending payment {pay.get('id')}!")
                            
                            details_text = f"Email ({em.get('from', '')}): {parsed.get('content') or em.get('subject', '')}"
                            txn_date = parsed.get("date") or em.get("date", "")

                            success = gateway_db.add_processed_transaction(
                                trans_no=trans_no,
                                amount=credit_amount,
                                details=details_text,
                                date=txn_date
                            )
                            
                            if success:
                                pay_id = pay.get('id')
                                if pay_id:
                                    gateway_db.update_pending_payment_status(pay_id, 'completed')
                                class DummyTxn:
                                    creditAmount = str(credit_amount)
                                    refNo = trans_no
                                    description = f"Email [{em.get('subject', '')}]: {parsed.get('content') or 'Không có'}"
                                    transactionDate = txn_date
                                
                                # Send push webhook (success event) & Telegram notification
                                await send_payment_webhook(pay, status="completed", transaction=DummyTxn())
                                
                                processed_count += 1
                                matched = True
                                break

                    # If email contains bank transaction but no order matched, send Telegram alert & record transaction
                    if not matched:
                        txn_date = parsed.get("date") or em.get("date", "")
                        gateway_db.add_processed_transaction(
                            trans_no=trans_no,
                            amount=credit_amount,
                            details=f"Unmatched Email ({em.get('from', '')}): {parsed.get('content') or em.get('subject', '')}",
                            date=txn_date
                        )
                        unmatched_msg = (
                            f"📥 <b>[KOS GATEWAY] NHẬN EMAIL NGÂN HÀNG MỚI</b>\n"
                            f"---------------------------------\n"
                            f"✉️ <b>Tiêu đề:</b> {em.get('subject', 'Không tiêu đề')}\n"
                            f"👤 <b>Người gửi:</b> {em.get('from', 'N/A')}\n"
                            f"💰 <b>Số tiền trích xuất:</b> +{credit_amount:,.0f} VNĐ\n"
                            f"📝 <b>Nội dung trích xuất:</b> <code>{parsed.get('content') or 'Không có'}</code>\n"
                            f"💳 <b>Mã giao dịch:</b> <code>{trans_no}</code>\n"
                            f"ℹ️ <i>Trạng thái: Đã bóc tách email thành công (Chưa có đơn hàng chờ khớp).</i>"
                        )
                        await send_telegram_notification(unmatched_msg)
        except Exception as e:
            logger.error(f"Error checking email bank transactions: {e}", exc_info=True)

    return processed_count
