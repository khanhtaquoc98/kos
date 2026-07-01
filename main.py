import os
import logging
import asyncio
import uuid
import hashlib
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel

import database

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mbbank-webhook")

app = FastAPI(title="MB Bank Webhook Gateway", version="0.3.0")

# Setup templates path (in the same directory)
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)

@app.middleware("http")
async def check_db_initialization(request: Request, call_next):
    if database.db_initialization_error:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": database.db_initialization_error}
            )
        else:
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Lỗi Khởi Tạo Database - KOS</title>
                <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700&display=swap" rel="stylesheet">
                <style>
                    body {{ background: #0b0f19; color: #f3f4f6; font-family: 'Plus Jakarta Sans', sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }}
                    .card {{ background: rgba(17, 24, 39, 0.7); border: 1px solid rgba(239, 68, 68, 0.2); padding: 40px; border-radius: 20px; max-width: 500px; width: 100%; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5); backdrop-filter: blur(10px); }}
                    h2 {{ color: #ef4444; margin-top: 0; }}
                    p {{ color: #9ca3af; font-size: 0.95rem; line-height: 1.6; margin-bottom: 24px; text-align: left; }}
                    pre {{ background: rgba(0,0,0,0.3); padding: 16px; border-radius: 10px; text-align: left; overflow-x: auto; font-family: monospace; font-size: 0.85rem; border: 1px solid rgba(255,255,255,0.05); color: #e5e7eb; }}
                </style>
            </head>
            <body>
                <div class="card">
                    <div style="font-size: 3rem; margin-bottom: 20px;">⚠️</div>
                    <h2>Lỗi Kết Nối / Cấu Hình Supabase</h2>
                    <p>Ứng dụng không thể kết nối hoặc khởi tạo bảng dữ liệu trên Supabase:</p>
                    <pre>{database.db_initialization_error}</pre>
                    <p style="margin-top: 20px;"><b>Hướng dẫn khắc phục:</b><br>
                    1. Vào Vercel Settings -> Environment Variables, điền đúng <code>SUPABASE_URL</code> và <code>SUPABASE_KEY</code>.<br>
                    2. Kiểm tra xem bạn đã copy nội dung file <code>schema.sql</code> và bấm <b>Run</b> trong <b>Supabase SQL Editor</b> hay chưa.</p>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=500)
    return await call_next(request)

# Global variables for session/cache
# In production, use database configs or redis, but we'll fetch from db config table
# We keep active bank clients cached to avoid re-authenticating every request
bank_clients = {}  # username -> MBBank

# Initialize Database on startup
@app.on_event("startup")
def startup_db():
    try:
        database.init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

def run_in_thread(func, *args, **kwargs):
    """
    Wraps a synchronous function call inside a background thread pool,
    ensuring that a thread-local asyncio event loop is set up to support
    mbbank-lib's internal WASM/Go-js event loop needs.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return func(*args, **kwargs)

# Session Helper (simple secure cookie-based auth)
SESSION_TOKEN = str(uuid.uuid4())

def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if not token or token != SESSION_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"}
        )
    return True

# ----------------- Models -----------------

class QRRequest(BaseModel):
    reference_id: str
    amount: float
    content: str
    callback_url: Optional[str] = None

class ConfigUpdate(BaseModel):
    mb_username: str
    mb_password: str
    mb_account_number: Optional[str] = ""
    default_callback_url: Optional[str] = ""
    callback_secret: str
    admin_password: str

# ----------------- Auth Routes -----------------

@app.get("/", response_class=HTMLResponse)
async def index_redirect():
    return RedirectResponse(url="/admin")

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login_post(password: str = Form(...)):
    admin_pass = database.get_config("admin_password", "admin123")
    if password == admin_pass:
        response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="session_token", value=SESSION_TOKEN, httponly=True, max_age=3600*24)
        return response
    
    return templates.TemplateResponse("login.html", {
        "request": {},
        "error": "Mật khẩu Admin không chính xác!"
    })

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_token")
    return response

# ----------------- Admin Panel -----------------

@app.get("/admin", response_class=HTMLResponse)
async def admin_get(request: Request, authenticated: bool = Depends(get_current_user)):
    configs = database.get_all_configs()
    pending = [p for p in database.get_pending_payments() if p["status"] == "pending"]
    processed = database.get_recent_processed_transactions()
    
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "configs": configs,
        "pending_payments": pending,
        "processed_transactions": processed
    })

@app.get("/demo", response_class=HTMLResponse)
async def demo_get(request: Request):
    """Public demo page representing the checkout QR code page for users."""
    account_number = database.get_config("mb_account_number") or database.get_config("mb_username")
    
    # Attempt to fetch owner name from active client session
    account_name = "Chưa kết nối Bank"
    username = database.get_config("mb_username")
    if username and username in bank_clients:
        try:
            client = bank_clients[username]
            user_info = await asyncio.to_thread(run_in_thread, client.userinfo)
            account_name = user_info.cust.nm
        except Exception:
            pass
            
    return templates.TemplateResponse("demo.html", {
        "request": request,
        "account_number": account_number,
        "account_name": account_name
    })

@app.get("/checkout", response_class=HTMLResponse)
async def checkout_get(
    request: Request,
    amount: float = 0.0,
    content: str = "",
    callback: str = "",
    cancel_url: str = "",
    orderCode: str = "",
    orderId: str = "",
    webhook_url: Optional[str] = None
):
    if not amount or not content or not orderId:
        return templates.TemplateResponse("checkout_error.html", {
            "request": request,
            "error": "Thiếu tham số thanh toán bắt buộc (số tiền amount, nội dung content, hoặc mã đơn hàng orderId)."
        })
        
    content = content.upper().strip()
    
    # Check if this pending payment already exists in database
    existing_status = database.get_pending_payment_status(orderId)
    if not existing_status or existing_status != 'pending':
        payment_id = str(uuid.uuid4())
        try:
            database.add_pending_payment(
                payment_id=payment_id,
                reference_id=orderId,
                amount=amount,
                content=content,
                callback_url=webhook_url
            )
            # Trigger async check to quickly see if it's already in the bank
            asyncio.create_task(perform_transaction_check())
            logger.info(f"Registered pending payment via checkout: ref={orderId}, amount={amount}, content={content}")
        except Exception as e:
            logger.error(f"Error registering pending payment in checkout: {e}")

    account_number = database.get_config("mb_account_number") or database.get_config("mb_username")
    
    # Attempt to fetch owner name from active client session
    account_name = "Chưa kết nối Bank"
    username = database.get_config("mb_username")
    if username and username in bank_clients:
        try:
            client = bank_clients[username]
            user_info = await asyncio.to_thread(run_in_thread, client.userinfo)
            account_name = user_info.cust.nm
        except Exception:
            pass

    return templates.TemplateResponse("checkout.html", {
        "request": request,
        "amount": amount,
        "content": content,
        "callback": callback,
        "cancel_url": cancel_url,
        "orderCode": orderCode,
        "orderId": orderId,
        "account_number": account_number,
        "account_name": account_name
    })

@app.post("/admin/config")
async def admin_config_post(cfg: ConfigUpdate, authenticated: bool = Depends(get_current_user)):
    try:
        database.set_config("mb_username", cfg.mb_username)
        database.set_config("mb_password", cfg.mb_password)
        database.set_config("mb_account_number", cfg.mb_account_number or "")
        database.set_config("default_callback_url", cfg.default_callback_url or "")
        database.set_config("callback_secret", cfg.callback_secret)
        database.set_config("admin_password", cfg.admin_password)
        
        # Invalidate cached client to force refresh with new credentials
        if cfg.mb_username in bank_clients:
            del bank_clients[cfg.mb_username]
            
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ----------------- API Endpoints -----------------

async def get_mb_client() -> "MBBank":
    """Helper to instantiate and return cached/new MBBank client."""
    from mbbank import MBBank
    username = database.get_config("mb_username")
    password = database.get_config("mb_password")
    
    if not username or not password:
        raise HTTPException(status_code=400, detail="Vui lòng cấu hình tài khoản MB Bank trong Dashboard Admin trước.")
        
    if username in bank_clients:
        return bank_clients[username]
        
    # Instantiate new client
    client = MBBank(username=username, password=password)
    bank_clients[username] = client
    return client

@app.post("/api/test-mb")
async def test_mb_connection(authenticated: bool = Depends(get_current_user)):
    """Tests connection to MB Bank, retrieving username and account balance."""
    try:
        client = await get_mb_client()
        # Trigger login/refresh if needed
        user_info = await asyncio.to_thread(run_in_thread, client.userinfo)
        balances = await asyncio.to_thread(run_in_thread, client.getBalance)
        
        return {
            "success": True,
            "account_name": user_info.cust.nm,
            "balances": [
                {
                    "account_no": ac.acctNo,
                    "balance": ac.currentBalance,
                    "currency": ac.ccyCd
                } for ac in balances.acct_list
            ]
        }
    except Exception as e:
        logger.error(f"Error testing MB connection: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/scan-now")
async def scan_now_endpoint(authenticated: bool = Depends(get_current_user)):
    """Forces an immediate manual scan of recent transactions for match check."""
    try:
        count = await perform_transaction_check()
        return {"success": True, "processed_count": count}
    except Exception as e:
        logger.error(f"Manual scan failed: {e}")
        return {"success": False, "error": str(e)}

@app.delete("/api/pending-payments/{payment_id}")
async def delete_pending_payment_endpoint(payment_id: str, authenticated: bool = Depends(get_current_user)):
    """Deletes a pending payment from the queue."""
    try:
        database.delete_pending_payment(payment_id)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting pending payment {payment_id}: {e}")
        return {"success": False, "error": str(e)}

@app.delete("/api/pending-payments")
async def delete_all_pending_payments_endpoint(authenticated: bool = Depends(get_current_user)):
    """Deletes all pending payments from the queue."""
    try:
        database.delete_all_pending_payments()
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting all pending payments: {e}")
        return {"success": False, "error": str(e)}

@app.get("/api/cron")
async def cron_trigger(secret: Optional[str] = None, request: Request = None):
    """Secure endpoint for Vercel Cron or other automated pollers to trigger check."""
    expected_secret = os.environ.get("CRON_SECRET") or database.get_config("callback_secret")
    auth_header = request.headers.get("Authorization")
    header_token = None
    if auth_header and auth_header.startswith("Bearer "):
        header_token = auth_header.split(" ")[1]
        
    if secret != expected_secret and header_token != expected_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid secret token"
        )
        
    try:
        count = await perform_transaction_check()
        return {"success": True, "processed_count": count}
    except Exception as e:
        logger.error(f"Cron scan failed: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/webhook/check-qr")
async def register_qr_payment(req: QRRequest):
    """
    Registers a QR code for payment verification.
    If already paid (found in recent processed logs), returns immediately.
    Otherwise, queues it in pending_payments so active scans will cross-check it.
    """
    try:
        # Check if this reference has already been matched
        # Check by content pattern matching
        # Wait, since the bank descriptions are checked periodically,
        # we can first register it and then run a scan
        payment_id = str(uuid.uuid4())
        
        # Save to database
        database.add_pending_payment(
            payment_id=payment_id,
            reference_id=req.reference_id,
            amount=req.amount,
            content=req.content.upper().strip(),
            callback_url=req.callback_url
        )
        
        # Trigger async check to quickly see if it's already in the bank
        asyncio.create_task(perform_transaction_check())
        
        return {
            "success": True,
            "payment_id": payment_id,
            "status": "pending",
            "message": "QR registered. System is actively scanning for transaction."
        }
    except Exception as e:
        logger.error(f"Error registering QR check: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/check-payment/{reference_id}")
async def check_payment_status(reference_id: str, force: bool = False):
    """
    Directly triggers a scan and checks if a specific reference_id has been paid.
    Suitable for frontend polling or when user clicks 'I have transferred' button.
    """
    # Force query MB Bank transactions right now to see if money arrived
    await perform_transaction_check(force=force)
    
    # Query database to see if status updated
    status = database.get_pending_payment_status(reference_id)
    
    if not status:
        return {"status": "not_found", "message": "Không tìm thấy yêu cầu thanh toán với reference_id này."}
        
    return {
        "reference_id": reference_id,
        "status": status  # 'pending' or 'completed'
    }

# ----------------- Core Synchronization Engine -----------------

async def perform_transaction_check(force: bool = False) -> int:
    """
    Core engine function.
    Fetches recent transactions from MB Bank and matches against pending_payments in the database.
    Sends callback webhooks to registered endpoints for matched items.
    """
    username = database.get_config("mb_username")
    if not username:
        logger.warning("MB Bank username not configured. Skipping scan.")
        return 0
        
    try:
        import time
        import json
        from mbbank.modals.transaction_history import Transaction

        # Check cache validity (60 seconds)
        last_scan_str = database.get_config("last_bank_scan_time")
        cache_valid = False
        txn_list = []
        
        if last_scan_str and not force:
            try:
                last_scan_time = float(last_scan_str)
                if time.time() - last_scan_time < 60.0:
                    cache_valid = True
                    logger.info("Using cached bank transactions (cache age < 60s)")
                    cache_data = database.get_config("bank_transactions_cache")
                    if cache_data:
                        txn_list_raw = json.loads(cache_data)
                        txn_list = [Transaction.model_validate(t) for t in txn_list_raw]
            except Exception as e:
                logger.error(f"Error reading transaction cache: {e}")
                cache_valid = False

        if not cache_valid:
            client = await get_mb_client()
            
            # Fetch transactions only for today (ngày hôm nay từ 00:00:00)
            to_date = datetime.now()
            from_date = to_date.replace(hour=0, minute=0, second=0, microsecond=0)
            
            logger.info(f"Cache expired/invalid. Scanning MB Bank transactions from {from_date} to {to_date}...")
            
            # Call bank API in threadpool
            account_no = database.get_config("mb_account_number") or username
            history = await asyncio.to_thread(
                run_in_thread,
                client.getTransactionAccountHistory,
                accountNo=account_no,
                from_date=from_date,
                to_date=to_date
            )
            
            txn_list = history.transactionHistoryList or []
            logger.info(f"Retrieved {len(txn_list)} transactions from MB Bank.")
            
            # Save to cache in database (Supabase)
            try:
                txn_list_dicts = [t.model_dump() for t in txn_list]
                database.set_config("bank_transactions_cache", json.dumps(txn_list_dicts))
                database.set_config("last_bank_scan_time", str(time.time()))
                logger.info("Saved transactions to database cache.")
            except Exception as e:
                logger.error(f"Error saving transaction cache to database: {e}")
        
        # Fetch pending payments
        pending = [p for p in database.get_pending_payments() if p["status"] == "pending"]
        if not pending:
            logger.info("No pending payments in database. Nothing to check.")
            return 0
            
        processed_count = 0
        
        for txn in txn_list:
            # We care only about credit (incoming money)
            credit_amount = float(txn.creditAmount or 0)
            if credit_amount <= 0:
                continue
                
            trans_no = txn.refNo
            desc = (getattr(txn, "description", "") or getattr(txn, "addDescription", "") or "").upper().strip()
            
            # Check if this transaction has already been processed
            if database.is_transaction_processed(trans_no):
                continue
                
            # Try to match with pending payments
            for pay in pending:
                if pay['status'] != 'pending':
                    continue
                    
                # Match criteria: description matches the content and amount matches
                pay_content = pay['content'].upper().strip()
                
                # Check for exact or substring match in description
                content_matched = pay_content in desc
                amount_matched = abs(float(pay['amount']) - credit_amount) < 1.0  # allow minor float delta
                
                if content_matched and amount_matched:
                    logger.info(f"MATCH FOUND: Trans {trans_no} matches pending payment {pay['id']}!")
                    
                    # Mark transaction as processed to prevent double callback
                    success = database.add_processed_transaction(
                        trans_no=trans_no,
                        amount=credit_amount,
                        details=getattr(txn, "description", "") or getattr(txn, "addDescription", "") or "",
                        date=txn.transactionDate or ""
                    )
                    
                    if success:
                        # Update payment status
                        database.update_pending_payment_status(pay['id'], 'completed')
                        
                        # Trigger webhook callback in background
                        callback_url = pay['callback_url'] or database.get_config("default_callback_url")
                        if callback_url:
                            asyncio.create_task(send_callback_webhook(callback_url, pay, txn))
                            
                        processed_count += 1
                        break  # Break inner loop, transaction matched
                        
        return processed_count
    except Exception as e:
        logger.error(f"Error performing transaction check: {e}")
        return 0

async def send_callback_webhook(url: str, payment: dict, transaction: any):
    """Sends a signed HTTP POST callback to the client website."""
    secret = database.get_config("callback_secret", "super-secret-callback-token")
    
    payload = {
        "status": "success",
        "reference_id": payment["reference_id"],
        "payment_id": payment["id"],
        "amount": float(transaction.creditAmount),
        "trans_no": transaction.refNo,
        "description": getattr(transaction, "description", "") or getattr(transaction, "addDescription", "") or "",
        "date": transaction.transactionDate or "",
        "timestamp": int(datetime.utcnow().timestamp())
    }
    
    # Generate signature for integrity and authenticity
    # sign_string = reference_id + payment_id + amount + trans_no + secret
    sign_str = f"{payload['reference_id']}{payload['payment_id']}{payload['amount']}{payload['trans_no']}{secret}"
    signature = hashlib.sha256(sign_str.encode()).hexdigest()
    payload["signature"] = signature
    
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature
    }
    
    logger.info(f"Sending webhook to {url} for ref {payment['reference_id']}...")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload, headers=headers)
            if res.status_code in [200, 201]:
                logger.info(f"Webhook delivered successfully to {url}. Status: {res.status_code}")
            else:
                logger.error(f"Webhook delivery failed. Server returned status {res.status_code}: {res.text}")
    except Exception as e:
        logger.error(f"Failed to connect to callback URL {url}: {e}")
