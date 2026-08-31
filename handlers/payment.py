import uuid
import logging
import asyncio
from datetime import datetime
from urllib.parse import quote
import json
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

import gateway_db
import email_engine
from dependencies import (
    CreatePaymentRequest,
    CancelPaymentRequest,
    QRRequest,
    SimulatePaymentRequest,
    SandboxEmailSimulateRequest
)
from services.sync_service import (
    perform_transaction_check,
    send_payment_webhook,
    subscribe_sse,
    unsubscribe_sse
)

logger = logging.getLogger("mbbank-webhook.payment")

router = APIRouter(tags=["Payment"])

@router.post("/api/v1/payment/create")
@router.post("/api/payment/create")
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
            callback_url=req.callback_url or ""
        )
        asyncio.create_task(perform_transaction_check())
        logger.info(f"Created new payment order: order_id={order_ref}, amount={req.amount}, content={content}")
    else:
        payment_id = existing["id"]

    return_redirect = req.return_url or req.callback_url or ""
    base_url = str(request.base_url).rstrip('/')
    checkout_url = f"{base_url}/checkout?orderId={quote(order_ref)}&amount={req.amount}&content={quote(content)}"
    if return_redirect:
        checkout_url += f"&callback={quote(return_redirect)}"

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

@router.post("/api/v1/payment/cancel")
@router.post("/api/payment/cancel")
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

@router.post("/api/webhook/check-qr")
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
            callback_url=req.callback_url
        )
        return await create_payment_order(create_req, request)
    except Exception as e:
        logger.error(f"Error registering QR check: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/check-payment/{reference_id}")
async def check_payment_status(reference_id: str, force: bool = False):
    """
    Directly checks if a specific reference_id has been paid.
    Returns status immediately if already completed/cancelled.
    Triggers bank scan if forced.
    """
    status_str = gateway_db.get_pending_payment_status(reference_id)
    if status_str and status_str != "pending":
        return {
            "reference_id": reference_id,
            "status": status_str
        }

    if force:
        await perform_transaction_check(force=True)
    
    status_str = gateway_db.get_pending_payment_status(reference_id)
    if not status_str:
        return {"status": "not_found", "message": "Không tìm thấy yêu cầu thanh toán với reference_id này."}
        
    return {
        "reference_id": reference_id,
        "status": status_str
    }

@router.get("/api/payment/stream/{reference_id}")
async def payment_event_stream(reference_id: str, request: Request):
    """
    Server-Sent Events (SSE) stream for instant realtime payment status updates.
    Eliminates polling delays and pushes payment completion to browser in <10ms.
    """
    async def event_generator():
        # Check current DB status first
        status_str = gateway_db.get_pending_payment_status(reference_id)
        if status_str and status_str != "pending":
            yield f"data: {json.dumps({'status': status_str, 'reference_id': reference_id})}\n\n"
            return

        queue = subscribe_sse(reference_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                    if data.get("status") in ["completed", "success", "cancelled", "failed"]:
                        break
                except asyncio.TimeoutError:
                    # Heartbeat comment to keep SSE connection alive
                    yield ": heartbeat\n\n"
        finally:
            unsubscribe_sse(reference_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.post("/api/test-simulate-payment")
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

@router.post("/api/sandbox/simulate-email")
async def sandbox_simulate_email(req: SandboxEmailSimulateRequest):
    """
    Sandbox endpoint: Generates a realistic HTML bank email payload for the specified bank,
    runs it through the full regex parser and transaction matching pipeline,
    updates DB status, sends merchant push webhook, and notifies Telegram.
    """
    bank_type = (req.bank or "mbbank").lower().strip()
    amount = float(req.amount)
    content = req.content.strip()
    trans_no = req.trans_no or f"FT{int(datetime.utcnow().timestamp())}"
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    amt_formatted = f"{int(amount):,}".replace(",", ".")

    if bank_type == "mbbank":
        sender = "notification@mbbank.com.vn"
        html_tpl = f"""
        <html><body>
        <h3>THÔNG BÁO BIẾN ĐỘNG SỐ DƯ TÀI KHOẢN MB BANK</h3>
        <p>Quý khách vừa có giao dịch ghi có tài khoản tại MB Bank.</p>
        <table>
            <tr><td>Số tiền ghi có:</td><td>+{amt_formatted} VND</td></tr>
            <tr><td>Nội dung chuyển khoản:</td><td>{content}</td></tr>
            <tr><td>Mã giao dịch:</td><td>{trans_no}</td></tr>
            <tr><td>Thời gian giao dịch:</td><td>{now_str}</td></tr>
        </table>
        </body></html>
        """
    elif bank_type == "vietcombank":
        sender = "vietcombank.com.vn"
        html_tpl = f"""
        <html><body>
        <h2>VIETCOMBANK - THÔNG BÁO GIAO DỊCH GHI CÓ</h2>
        <p>Số tiền ghi có: +{amt_formatted} VND</p>
        <p>Nội dung GD: {content}</p>
        <p>Mã GD: {trans_no}</p>
        <p>Thời gian GD: {now_str}</p>
        </body></html>
        """
    elif bank_type == "techcombank":
        sender = "techcombank.com.vn"
        html_tpl = f"""
        <html><body>
        <h2>TECHCOMBANK - THÔNG BÁO GHI CÓ</h2>
        <p>Số tiền ghi có: +{amt_formatted} VND</p>
        <p>Nội dung giao dịch: {content}</p>
        <p>Mã GD: {trans_no}</p>
        <p>Thời gian GD: {now_str}</p>
        </body></html>
        """
    elif bank_type == "timo":
        sender = "support@timo.vn"
        html_tpl = f"""
        <html><body>
        <p>TA QUOC KHANH thân mến,</p>
        <p>Tài khoản Spend Account vừa tăng {amt_formatted} VND vào {now_str}.</p>
        <p>Mô tả: {content}</p>
        <p>Trân trọng, Timo Digital Bank by BVBank</p>
        </body></html>
        """
    else:
        sender = "bank-notify@bank.com.vn"
        html_tpl = f"""
        <html><body>
        <p>Số tiền ghi có: +{amt_formatted} VND</p>
        <p>Nội dung: {content}</p>
        <p>Mã GD: {trans_no}</p>
        <p>Ngày GD: {now_str}</p>
        </body></html>
        """

    r_amt = gateway_db.get_config("email_parser_regex_amount")
    r_cnt = gateway_db.get_config("email_parser_regex_content")
    r_trn = gateway_db.get_config("email_parser_regex_trans_no")
    r_dat = gateway_db.get_config("email_parser_regex_date")

    parsed = email_engine.parse_bank_email_html(
        html_content=html_tpl,
        regex_amount=r_amt,
        regex_content=r_cnt,
        regex_trans_no=r_trn,
        regex_date=r_dat
    )
    if not parsed.get("trans_no"):
        parsed["trans_no"] = trans_no

    pending = [p for p in gateway_db.get_pending_payments() if isinstance(p, dict) and p.get("status") == "pending"]
    
    matched_payment = None
    if req.order_id:
        for p in pending:
            if p.get("order_id") == req.order_id or p.get("reference_id") == req.order_id or p.get("id") == req.order_id:
                matched_payment = p
                break

    if not matched_payment:
        for p in pending:
            p_cnt = (p.get("content") or "").upper().strip()
            p_amt = float(p.get("amount") or 0.0)
            if p_cnt and (p_cnt in content.upper() or content.upper() in p_cnt) and abs(p_amt - amount) < 1.0:
                matched_payment = p
                break

    match_result = False
    if matched_payment:
        success = gateway_db.add_processed_transaction(
            trans_no=trans_no,
            amount=amount,
            details=f"Sandbox Simulation ({bank_type.upper()}): {content}",
            date=now_str
        )
        if success:
            pay_id = matched_payment.get("id")
            if pay_id:
                gateway_db.update_pending_payment_status(pay_id, "completed")
            
            class DummyTxn:
                creditAmount = str(amount)
                refNo = trans_no
                description = content
                transactionDate = now_str

            asyncio.create_task(send_payment_webhook(matched_payment, status="completed", transaction=DummyTxn()))
            match_result = True

    return {
        "success": True,
        "is_sandbox": True,
        "bank": bank_type,
        "generated_sender": sender,
        "generated_html_sample": html_tpl.strip(),
        "parsed_result": parsed,
        "matched_payment": matched_payment,
        "match_success": match_result,
        "message": f"🧪 Sandbox: Đã giả lập email ngân hàng {bank_type.upper()} & đối soát duyệt đơn thành công!" if match_result else "🧪 Sandbox: Đã giả lập bóc tách email nhưng không tìm thấy đơn hàng chờ khớp (amount/content)."
    }
