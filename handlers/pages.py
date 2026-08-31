import os
import uuid
import logging
import asyncio
from fastapi import APIRouter, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse

import gateway_db
from dependencies import render_template, get_current_user, SESSION_TOKEN
from services.sync_service import perform_transaction_check

logger = logging.getLogger("mbbank-webhook.pages")

router = APIRouter(tags=["Pages"])

@router.get("/", response_class=HTMLResponse)
async def index_redirect():
    return RedirectResponse(url="/admin")

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return render_template(request, "login.html")

@router.post("/login")
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

@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_token")
    return response

@router.get("/admin", response_class=HTMLResponse)
async def admin_get(request: Request, authenticated: bool = Depends(get_current_user)):
    configs = gateway_db.get_all_configs()
    pending = [p for p in gateway_db.get_pending_payments() if p["status"] == "pending"]
    processed = gateway_db.get_recent_processed_transactions()
    
    return render_template(request, "admin.html", {
        "configs": configs,
        "pending_payments": pending,
        "processed_transactions": processed
    })

@router.get("/demo", response_class=HTMLResponse)
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

@router.get("/checkout", response_class=HTMLResponse)
async def checkout_get(
    request: Request,
    amount: float = 0.0,
    content: str = "",
    callback: str = "",
    orderCode: str = "",
    orderId: str = "",
    order_id: str = "",
    reference_id: str = ""
):
    final_order_id = orderId or order_id or orderCode or reference_id
    if not amount or not content or not final_order_id:
        return render_template(request, "checkout_error.html", {
            "error": "Thiếu tham số thanh toán bắt buộc (số tiền amount, nội dung content, hoặc mã đơn hàng orderId)."
        })
        
    content = content.upper().strip()
    
    # Check if this pending payment already exists in database
    existing_status = gateway_db.get_pending_payment_status(final_order_id)
    if existing_status in ['completed', 'success']:
        target_cb = callback or gateway_db.get_config("default_callback_url", "")
        if target_cb:
            connector = '&' if '?' in target_cb else '?'
            redirect_target = f"{target_cb}{connector}orderCode={final_order_id}&status=completed"
            logger.info(f"Order {final_order_id} already completed, redirecting directly to {redirect_target}")
            return RedirectResponse(url=redirect_target, status_code=status.HTTP_303_SEE_OTHER)

    if not existing_status:
        payment_id = str(uuid.uuid4())
        try:
            gateway_db.add_pending_payment(
                payment_id=payment_id,
                reference_id=final_order_id,
                amount=amount,
                content=content,
                callback_url=callback
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
