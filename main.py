import os
import re
import logging
import asyncio
import uuid
import hashlib
from datetime import datetime, timedelta
from typing import Optional
import base64

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from urllib.parse import quote
import httpx
import requests
from pydantic import BaseModel

import gateway_db
import email_engine

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mbbank-webhook")

app = FastAPI(title="KOS Admin Gateway", version="0.3.0")

# Setup templates path (in the same directory)
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
if not os.path.exists(templates_dir):
    try:
        os.makedirs(templates_dir, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed creating templates_dir: {e}")

templates = Jinja2Templates(directory=templates_dir)

def render_template(request: Request, name: str, context: dict = None, status_code: int = 200):
    if context is None:
        context = {}
    context["request"] = request
    
    import inspect
    sig = inspect.signature(templates.TemplateResponse)
    if "request" in sig.parameters:
        return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)
    else:
        return templates.TemplateResponse(name, context, status_code=status_code)

@app.middleware("http")
async def check_db_initialization(request: Request, call_next):
    if gateway_db.db_initialization_error:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": gateway_db.db_initialization_error}
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
                    <pre>{gateway_db.db_initialization_error}</pre>
                    <p style="margin-top: 20px;"><b>Hướng dẫn khắc phục:</b><br>
                    1. Vào Vercel Settings -> Environment Variables, điền đúng <code>SUPABASE_URL</code> và <code>SUPABASE_KEY</code>.<br>
                    2. Kiểm tra xem bạn đã copy nội dung file <code>schema.sql</code> và bấm <b>Run</b> trong <b>Supabase SQL Editor</b> hay chưa.</p>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=500)
    return await call_next(request)

# Initialize Database on startup
@app.on_event("startup")
def startup_db():
    try:
        gateway_db.init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

# Session Helper (simple secure cookie-based auth)
SESSION_TOKEN = str(uuid.uuid4())

def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if not token or token != SESSION_TOKEN:
        if request.url.path.startswith("/api/") or request.url.path.startswith("/admin/config"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Phiên đăng nhập đã hết hạn. Vui lòng tải lại trang để đăng nhập lại.")
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"}
        )
    return True

# ----------------- Models -----------------

class CreatePaymentRequest(BaseModel):
    order_id: Optional[str] = None
    reference_id: Optional[str] = None
    orderId: Optional[str] = None
    orderCode: Optional[str] = None
    amount: float
    content: str
    callback_url: Optional[str] = ""
    webhook_url: Optional[str] = ""

class CancelPaymentRequest(BaseModel):
    order_id: Optional[str] = None
    reference_id: Optional[str] = None
    reason: Optional[str] = "Giao dịch bị hủy"

class QRRequest(BaseModel):
    order_id: Optional[str] = None
    reference_id: Optional[str] = None
    amount: float
    content: str
    callback_url: Optional[str] = ""
    webhook_url: Optional[str] = ""

class ParseEmailRequest(BaseModel):
    html_content: str
    regex_amount: Optional[str] = None
    regex_content: Optional[str] = None
    regex_trans_no: Optional[str] = None
    regex_date: Optional[str] = None

class ConfigUpdate(BaseModel):
    mb_bank_code: Optional[str] = "MB"
    mb_account_number: Optional[str] = ""
    mb_account_name: Optional[str] = ""
    default_callback_url: Optional[str] = ""
    email_auth_method: Optional[str] = "oauth2"
    email_sender_filter: Optional[str] = ""
    gmail_pubsub_label_ids: Optional[str] = "BankNotify"
    gmail_pubsub_topic: Optional[str] = ""
    gmail_address: Optional[str] = ""
    gmail_app_password: Optional[str] = ""
    gmail_refresh_token: Optional[str] = ""
    telegram_bot_token: Optional[str] = ""
    telegram_chat_id: Optional[str] = ""
    telegram_notify_active: Optional[str] = "true"

# ----------------- Auth Routes -----------------

@app.get("/", response_class=HTMLResponse)
async def index_redirect():
    return RedirectResponse(url="/admin")

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return render_template(request, "login.html")

@app.post("/login")
async def login_post(request: Request, password: str = Form(...)):
    env_admin_pass = os.environ.get("ADMIN_PASSWORD")
    db_admin_pass = gateway_db.get_config("admin_password", "admin123")
    
    if (env_admin_pass and password == env_admin_pass) or (password == db_admin_pass):
        response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="session_token", value=SESSION_TOKEN, httponly=True, max_age=3600*24)
        return response
    
    return render_template(request, "login.html", {
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
    configs = gateway_db.get_all_configs()
    pending = [p for p in gateway_db.get_pending_payments() if p["status"] == "pending"]
    processed = gateway_db.get_recent_processed_transactions()
    
    return render_template(request, "admin.html", {
        "configs": configs,
        "pending_payments": pending,
        "processed_transactions": processed
    })

@app.get("/demo", response_class=HTMLResponse)
async def demo_get(request: Request):
    """Public demo page representing the checkout QR code page for users."""
    account_number = gateway_db.get_config("mb_account_number", "0123456789")
    account_name = gateway_db.get_config("mb_account_name", "CỔNG THANH TOÁN NGÂN HÀNG")
    bank_code = gateway_db.get_config("mb_bank_code", "MB")
    bank_names = {
        "MB": "MB Bank (Ngân Hàng Quân Đội)",
        "TIMO": "Timo Digital Bank (BVBank)",
        "VCB": "Vietcombank (Ngoại Thương)",
        "TCB": "Techcombank (Kỹ Thương)",
        "ACB": "ACB (Á Châu)",
        "VPB": "VPBank (Thịnh Vượng)",
        "TPB": "TPBank (Tiên Phong)",
        "BIDV": "BIDV",
        "CTG": "VietinBank",
        "STB": "Sacombank"
    }
    bank_name = bank_names.get(bank_code, "MB Bank")
            
    return render_template(request, "demo.html", {
        "account_number": account_number,
        "account_name": account_name,
        "bank_code": bank_code,
        "bank_name": bank_name
    })

@app.get("/checkout", response_class=HTMLResponse)
async def checkout_get(
    request: Request,
    amount: float = 0.0,
    content: str = "",
    callback: str = "",
    orderCode: str = "",
    orderId: str = "",
    order_id: str = "",
    reference_id: str = "",
    webhook_url: Optional[str] = None
):
    final_order_id = orderId or order_id or orderCode or reference_id
    if not amount or not content or not final_order_id:
        return render_template(request, "checkout_error.html", {
            "error": "Thiếu tham số thanh toán bắt buộc (số tiền amount, nội dung content, hoặc mã đơn hàng orderId)."
        })
        
    content = content.upper().strip()
    
    # Check if this pending payment already exists in database
    existing_status = gateway_db.get_pending_payment_status(final_order_id)
    if not existing_status or existing_status != 'pending':
        payment_id = str(uuid.uuid4())
        try:
            gateway_db.add_pending_payment(
                payment_id=payment_id,
                reference_id=final_order_id,
                amount=amount,
                content=content,
                callback_url=callback,
                webhook_url=webhook_url or ""
            )
            # Trigger async check to quickly see if it's already in the bank
            asyncio.create_task(perform_transaction_check())
            logger.info(f"Registered pending payment via checkout: ref={final_order_id}, amount={amount}, content={content}")
        except Exception as e:
            logger.error(f"Error registering pending payment in checkout: {e}")

    account_number = gateway_db.get_config("mb_account_number", "0934860931")
    account_name = gateway_db.get_config("mb_account_name", "TA QUOC KHANH")
    bank_code = gateway_db.get_config("mb_bank_code", "TIMO")

    return render_template(request, "checkout.html", {
        "amount": amount,
        "content": content,
        "callback": callback,
        "orderCode": orderCode or final_order_id,
        "orderId": final_order_id,
        "account_number": account_number,
        "account_name": account_name,
        "bank_code": bank_code
    })

@app.post("/admin/config")
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

# ----------------- API Endpoints -----------------

@app.post("/api/test-email-connection")
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

@app.post("/api/parse-email-sample")
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
        gateway_db.delete_pending_payment(payment_id)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting pending payment {payment_id}: {e}")
        return {"success": False, "error": str(e)}

@app.get("/api/oauth2/connect")
async def google_oauth_connect(request: Request, path: Optional[str] = "/api/auth/callback/google"):
    """Initiates Google OAuth2 SSO Authorization flow to connect Gmail account."""
    client_id = (gateway_db.get_config("gmail_client_id") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip().strip('"').strip("'")
    if not client_id:
        return HTMLResponse(content="""
        <script>
            alert("Chưa cấu hình GOOGLE_CLIENT_ID trong file .env!");
            window.location.href = "/admin";
        </script>
        """, status_code=400)

    base_url = str(request.base_url).rstrip('/')
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        base_url = f"{forwarded_proto}://{request.url.netloc}"

    callback_path = path if path.startswith('/') else f"/{path}"
    redirect_uri = f"{base_url}{callback_path}"
    scope = quote("https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/userinfo.email")
    
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={client_id}&"
        f"redirect_uri={quote(redirect_uri)}&"
        f"response_type=code&"
        f"scope={scope}&"
        f"access_type=offline&"
        f"prompt=consent"
    )
    logger.info(f"Initiating Google OAuth connect with redirect_uri: {redirect_uri}")
    return RedirectResponse(url=auth_url)

@app.get("/api/oauth2/callback")
@app.get("/api/auth/callback/google")
async def google_oauth_callback(request: Request, code: Optional[str] = None, error: Optional[str] = None):
    """Handles Google OAuth2 callback code exchange, fetches email and refresh token, and saves to DB."""
    if error or not code:
        logger.error(f"Google OAuth callback error: {error}")
        return RedirectResponse(url=f"/admin?oauth_error={quote(error or 'Người dùng hủy ủy quyền Google')}")
        
    client_id = (gateway_db.get_config("gmail_client_id") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip().strip('"').strip("'")
    client_secret = (gateway_db.get_config("gmail_client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip().strip('"').strip("'")
    
    base_url = str(request.base_url).rstrip('/')
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        base_url = f"{forwarded_proto}://{request.url.netloc}"

    path = request.url.path
    redirect_uri = f"{base_url}{path}"
    
    try:
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"
        }
        
        r = requests.post(token_url, data=token_data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code != 200:
            logger.error(f"Failed token exchange: status={r.status_code}, resp={r.text}")
            return RedirectResponse(url=f"/admin?oauth_error={quote('Lỗi đổi token từ Google API (' + str(r.status_code) + '): ' + r.text)}")
            
        res_data = r.json()
        access_token = res_data.get("access_token")
        refresh_token = res_data.get("refresh_token")
        
        # Get User Email from UserInfo API
        user_info_res = requests.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {access_token}"})
        gmail_address = ""
        if user_info_res.status_code == 200:
            gmail_address = user_info_res.json().get("email", "")
            
        if gmail_address:
            gateway_db.set_config("gmail_address", gmail_address)
        if refresh_token:
            gateway_db.set_config("gmail_refresh_token", refresh_token)
        gateway_db.set_config("email_auth_method", "oauth2")
        gateway_db.set_config("email_gateway_active", "true")
        
        logger.info(f"Successfully connected Google OAuth2 for Gmail: {gmail_address}")
        return RedirectResponse(url=f"/admin?oauth_success=true&email={quote(gmail_address)}")
        
    except Exception as e:
        logger.error(f"Google OAuth callback processing error: {e}")
        return RedirectResponse(url=f"/admin?oauth_error={quote(str(e))}")

@app.delete("/api/pending-payments")
async def delete_all_pending_payments_endpoint(authenticated: bool = Depends(get_current_user)):
    """Deletes all pending payments from the queue."""
    try:
        gateway_db.delete_all_pending_payments()
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting all pending payments: {e}")
        return {"success": False, "error": str(e)}

@app.get("/api/cron")
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

class SimulatePaymentRequest(BaseModel):
    order_id: str
    amount: Optional[float] = None

@app.post("/api/test-simulate-payment")
async def simulate_payment_success(req: SimulatePaymentRequest):
    """Simulates a bank transaction match for testing purposes."""
    pay = gateway_db.get_pending_payment_by_ref(req.order_id)
    if not pay:
        pending_list = gateway_db.get_pending_payments()
        for p in pending_list:
            if p.get("id") == req.order_id:
                pay = p
                break
                
    if not pay:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng trong hàng chờ.")

    trans_no = "SIM" + str(int(datetime.utcnow().timestamp()))
    amount = req.amount or float(pay.get("amount") or 0.0)
    txn_date = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    gateway_db.add_processed_transaction(
        trans_no=trans_no,
        amount=amount,
        details=f"Giả lập thanh toán test cho đơn hàng {req.order_id}",
        date=txn_date
    )
    gateway_db.update_pending_payment_status(pay["id"], "completed")

    class DummyTxn:
        creditAmount = str(amount)
        refNo = trans_no
        description = f"Giả lập thanh toán {pay.get('content')}"
        transactionDate = txn_date

    asyncio.create_task(send_payment_webhook(pay, status="completed", transaction=DummyTxn()))
    return {"success": True, "message": f"Đã giả lập duyệt thanh toán thành công cho đơn {req.order_id}!"}

@app.post("/api/v1/payment/create")
@app.post("/api/payment/create")
async def create_payment_order(req: CreatePaymentRequest, request: Request):
    """
    Creates a new payment order in KOS Gateway.
    Returns checkout URL, VietQR image URL, payment ID, and order details.
    """
    order_ref = req.order_id or req.reference_id or req.orderId or req.orderCode
    if not order_ref or not req.amount or not req.content:
        raise HTTPException(status_code=400, detail="Thiếu thông tin bắt buộc: order_id/reference_id, amount, content")
    
    content = req.content.upper().strip()
    payment_id = str(uuid.uuid4())
    
    # Check if existing order in database
    existing = gateway_db.get_pending_payment_by_ref(order_ref)
    if not existing or existing.get("status") != "pending":
        gateway_db.add_pending_payment(
            payment_id=payment_id,
            reference_id=order_ref,
            amount=req.amount,
            content=content,
            callback_url=req.callback_url or "",
            webhook_url=req.webhook_url or ""
        )
        asyncio.create_task(perform_transaction_check())
        logger.info(f"Created new payment order: order_id={order_ref}, amount={req.amount}, content={content}")
    else:
        payment_id = existing["id"]

    base_url = str(request.base_url).rstrip('/')
    checkout_url = f"{base_url}/checkout?orderId={quote(order_ref)}&amount={req.amount}&content={quote(content)}"
    if req.callback_url:
        checkout_url += f"&callback={quote(req.callback_url)}"
    if req.webhook_url:
        checkout_url += f"&webhook_url={quote(req.webhook_url)}"

    account_number = gateway_db.get_config("mb_account_number", "0123456789")
    account_name = gateway_db.get_config("mb_account_name", "CỔNG THANH TOÁN NGÂN HÀNG")
    bank_code = gateway_db.get_config("mb_bank_code", "MB")
    qr_code_url = f"https://img.vietqr.io/image/{bank_code}-{account_number}-compact2.png?amount={int(req.amount)}&addInfo={quote(content)}&accountName={quote(account_name)}"

    return {
        "success": True,
        "status": "pending",
        "order_id": order_ref,
        "payment_id": payment_id,
        "amount": req.amount,
        "content": content,
        "checkout_url": checkout_url,
        "qr_code_url": qr_code_url
    }

@app.post("/api/v1/payment/cancel")
@app.post("/api/payment/cancel")
async def cancel_payment_order(req: CancelPaymentRequest):
    """
    Cancels a pending payment order and sends a failure webhook push event to the client server.
    """
    order_ref = req.order_id or req.reference_id or req.orderId or req.orderCode
    if not order_ref:
        raise HTTPException(status_code=400, detail="Thiếu order_id hoặc reference_id")
    
    payment = gateway_db.get_pending_payment_by_ref(order_ref)
    if not payment:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng thanh toán")
    
    if payment["status"] == "pending":
        gateway_db.update_pending_payment_status(payment["id"], "cancelled")
        # Trigger failure push webhook
        asyncio.create_task(send_payment_webhook(payment, status="cancelled", reason=req.reason or "Đơn hàng bị hủy"))
        return {
            "success": True,
            "order_id": order_ref,
            "status": "cancelled",
            "message": "Đơn hàng thanh toán đã được hủy thành công."
        }
    
    return {
        "success": False,
        "order_id": order_ref,
        "status": payment["status"],
        "message": f"Không thể hủy đơn hàng vì trạng thái hiện tại là {payment['status']}"
    }

@app.post("/api/webhook/check-qr")
async def register_qr_payment(req: QRRequest, request: Request):
    """
    Registers a QR code for payment verification.
    """
    try:
        order_ref = req.order_id or req.reference_id or req.orderId or req.orderCode
        create_req = CreatePaymentRequest(
            order_id=order_ref,
            reference_id=order_ref,
            amount=req.amount,
            content=req.content,
            callback_url=req.callback_url,
            webhook_url=req.webhook_url
        )
        return await create_payment_order(create_req, request)
    except Exception as e:
        logger.error(f"Error registering QR check: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/check-payment/{reference_id}")
async def check_payment_status(reference_id: str, force: bool = False):
    """
    Directly triggers a scan and checks if a specific reference_id has been paid.
    Suitable for frontend polling or when user clicks 'I have transferred' button.
    """
    await perform_transaction_check(force=force)
    
    status = gateway_db.get_pending_payment_status(reference_id)
    if not status:
        return {"status": "not_found", "message": "Không tìm thấy yêu cầu thanh toán với reference_id này."}
        
    return {
        "reference_id": reference_id,
        "status": status
    }

# ----------------- Core Synchronization Engine -----------------

async def perform_transaction_check(force: bool = False) -> int:
    """
    Core engine function.
    Fetches recent transactions from Email (Gmail API / IMAP),
    matches against pending_payments in gateway_db,
    and sends callback webhooks to registered endpoints for matched items.
    """
    pending = [p for p in gateway_db.get_pending_payments() if isinstance(p, dict) and p.get("status") == "pending"]
    if not pending:
        logger.info("No pending payments in gateway_db. Nothing to check.")
        return 0

    processed_count = 0

    # Ensure Gmail Watch PubSub is active & up-to-date with configured labels
    asyncio.create_task(ensure_gmail_watch_active())

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
                                    description = parsed.get('content') or em.get('subject', '')
                                    transactionDate = txn_date
                                
                                # Send push webhook (success event)
                                asyncio.create_task(send_payment_webhook(pay, status="completed", transaction=DummyTxn()))
                                
                                processed_count += 1
                                break
        except Exception as e:
            logger.error(f"Error checking email bank transactions: {e}", exc_info=True)

    return processed_count

async def send_telegram_notification(text: str):
    """Sends HTML formatted log notification to configured Telegram Chat ID via Bot API."""
    bot_token = gateway_db.get_config("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = gateway_db.get_config("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
    notify_active = gateway_db.get_config("telegram_notify_active", "true")
    
    if not bot_token or not chat_id or str(notify_active).lower() == "false":
        return
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                logger.info(f"Telegram notification sent to chat_id {chat_id}")
            else:
                logger.error(f"Failed to send Telegram message: status={res.status_code}, resp={res.text}")
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")

@app.post("/api/test-telegram")
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
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                return {"success": True, "message": f"Đã gửi tin nhắn test thành công tới Telegram Chat ID {chat_id}!"}
            else:
                return {"success": False, "error": f"Lỗi từ Telegram API (HTTP {res.status_code}): {res.text}"}
    except Exception as e:
        return {"success": False, "error": f"Lỗi kết nối Telegram: {str(e)}"}

async def send_payment_webhook(payment: dict, status: str, transaction: Optional[any] = None, reason: str = ""):
    """
    Sends a signed HTTP POST push webhook to the client server for both SUCCESS and FAILURE/CANCEL events.
    Also triggers Telegram notification to admin chat.
    """
    secret = gateway_db.get_config("callback_secret", "super-secret-callback-token")
    
    target_url = payment.get("webhook_url") or payment.get("callback_url") or gateway_db.get_config("default_callback_url")
    ref_id = payment.get("reference_id") or payment.get("id")
    p_id = payment.get("id")
    amount = float(payment.get("amount") or 0.0)
    
    trans_no = getattr(transaction, "refNo", "") if transaction else ""
    desc = getattr(transaction, "description", "") or getattr(transaction, "addDescription", "") if transaction else (reason or payment.get("content", ""))
    txn_date = getattr(transaction, "transactionDate", "") if transaction else ""

    is_success = (status == "completed")
    event_type = "payment.success" if is_success else "payment.failed"

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
    
    # Send Webhook to Merchant App FIRST for minimum latency
    if target_url:
        logger.info(f"Sending push webhook [{event_type}] to {target_url} for order {ref_id}...")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(target_url, json=payload, headers=headers)
                if res.status_code in [200, 201]:
                    logger.info(f"Webhook delivered successfully to {target_url}. Status: {res.status_code}")
                else:
                    logger.error(f"Webhook delivery failed to {target_url}. Status: {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"Failed to connect to webhook URL {target_url}: {e}")

    # Send Telegram notification in background
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
    asyncio.create_task(send_telegram_notification(tele_msg))


@app.post("/api/webhooks/gmail-push")
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
            decoded_data = base64.b64decode(data_b64).decode("utf-8")
            logger.info(f"Received Gmail Pub/Sub Push notification: {decoded_data}")
        
        # Run transaction scan synchronously on Vercel Serverless before response terminates
        count = await perform_transaction_check(force=True)
        return {"success": True, "message": "Gmail Push notification received", "processed_count": count}
    except Exception as e:
        logger.error(f"Error handling Gmail Push notification: {e}")
        return {"success": False, "error": str(e)}


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

@app.post("/api/admin/gmail-watch/subscribe")
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)




