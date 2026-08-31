import os
import uuid
import logging
from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import gateway_db

logger = logging.getLogger("mbbank-webhook")

# Setup templates path (in the same root directory)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates_dir = os.path.join(BASE_DIR, "templates")
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

# Session Helper (simple secure cookie-based auth)
SESSION_TOKEN = str(uuid.uuid4())

def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if not token or token != SESSION_TOKEN:
        if request.url.path.startswith("/api/") or request.url.path.startswith("/admin/config"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Phiên đăng nhập đã hết hạn. Vui lòng tải lại trang để đăng nhập lại."
            )
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"}
        )
    return True

# ----------------- Shared Pydantic Schemas -----------------

class CreatePaymentRequest(BaseModel):
    order_id: Optional[str] = None
    reference_id: Optional[str] = None
    orderId: Optional[str] = None
    orderCode: Optional[str] = None
    amount: float
    content: str
    callback_url: Optional[str] = ""
    return_url: Optional[str] = ""
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

class SimulatePaymentRequest(BaseModel):
    order_id: str
    amount: Optional[float] = None

class SandboxEmailSimulateRequest(BaseModel):
    bank: Optional[str] = "mbbank"
    amount: float
    content: str
    trans_no: Optional[str] = None
    order_id: Optional[str] = None
