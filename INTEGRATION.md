# 📖 Hướng Dẫn Tích Hợp Cổng Thanh Toán KOS Webhook Gateway

Tài liệu này hướng dẫn chi tiết cách tích hợp **KOS Webhook Gateway** vào website hoặc ứng dụng bán hàng (Node.js, Next.js, PHP, Python, Laravel, v.v.).

---

> [!IMPORTANT]
> **Hỏi: Nếu khách hàng mở App Bank quét QR xong rồi TẮT TAB / TẮT TRÌNH DUYỆT thì Website bán hàng có nhận được Webhook không?**
>
> **Trả lời: CÓ, VẪN NHẬN 100%!**
> Push Webhook là giao tiếp **Server-to-Server** trực tiếp giữa KOS Gateway Server và Server Website Bán Hàng của bạn. Nó hoạt động hoàn toàn độc lập ở Backend, không hề phụ thuộc vào việc trình duyệt của khách hàng đang mở hay đã tắt. Ngay khi Ngân hàng báo số dư, KOS Server sẽ gửi HTTP POST báo về Server bán hàng để tự động duyệt đơn.

---

## ⚡ Tổng Quan Luồng Thanh Toán (Dual Notification Workflow)

Hệ thống hoạt động theo cơ chế **Dual Notification**:
1. **Push Webhook (Server-to-Server)**: Gửi sự kiện HTTP POST kèm chữ ký bảo mật SHA-256 về `webhook_url` cho **cả 2 trường hợp**: **Thành công (`payment.success`)** và **Thất bại/Hủy (`payment.failed`)**.
2. **Browser Callback (Client Redirect)**: Tự động điều hướng trình duyệt của khách hàng về `callback_url` (khi chuyển khoản thành công) hoặc `cancel_url` (khi bấm hủy hoặc hết hạn).

```
Website Bán Hàng (Client)                    KOS Webhook Gateway
       │                                              │
       ├─── 1. POST /api/v1/payment/create ──────────►│ (Đăng ký đơn hàng mới)
       │◄── Trả về checkout_url & qr_code_url ────────┤
       │                                              │
  (Mở trang checkout_url cho người dùng)              │ (Nhận email chuyển khoản từ Bank)
       │                                              │ ──► Parse HTML Email (MB, VCB, TCB, Timo...)
       │                                              │
       │◄── 2A. PUSH WEBHOOK (payment.success) ───────┤ (Nếu chuyển tiền KHỚP amount & content)
       │    (Server-to-Server POST kèm Signature)    │
       │                                              │
 (Browser redirect -> callback_url)                   │
       │                                              │
       │◄── 2B. PUSH WEBHOOK (payment.failed) ────────┤ (Nếu hủy đơn hoặc hết hạn)
       │    (Server-to-Server POST kèm Signature)    │
       │                                              │
 (Browser redirect -> cancel_url)                     │
```

---

## 🚀 1. Tạo Đơn Hàng Thanh Toán (`POST /api/v1/payment/create`)

Khi người dùng nhấn **Thanh Toán** trên website của bạn, Backend gửi request HTTP POST để khởi tạo đơn hàng:

* **Endpoint**: `POST https://your-kos-domain.vercel.app/api/v1/payment/create`
* **Content-Type**: `application/json`

### Request Body Payload
```json
{
  "order_id": "ORDER_10023",
  "amount": 500000,
  "content": "PAY ORDER 10023",
  "callback_url": "https://myshop.com/payment/result",
  "webhook_url": "https://myshop.com/api/kos-webhook"
}
```

| Tham số | Kiểu dữ liệu | Bắt buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `order_id` | `string` | **Có** | Mã đơn hàng duy nhất từ hệ thống của bạn (hoặc `reference_id`) |
| `amount` | `number` | **Có** | Số tiền thanh toán (VNĐ) |
| `content` | `string` | **Có** | Nội dung chuyển khoản (không dấu, ví dụ: `PAY ORDER 10023`) |
| `callback_url` | `string` | Không | URL điều hướng trình duyệt (kèm `?status=completed` hoặc `?status=cancelled`) |
| `webhook_url` | `string` | Không | URL nhận Push Webhook HTTP POST (Server-to-Server) |

### Response Payload (Khi thành công)
```json
{
  "success": true,
  "status": "pending",
  "order_id": "ORDER_10023",
  "payment_id": "f8a92b1c-0e8e-4af9-929a-61cb83517d8e",
  "amount": 500000.0,
  "content": "PAY ORDER 10023",
  "checkout_url": "https://your-kos-domain.vercel.app/checkout?orderId=ORDER_10023&amount=500000&content=PAY%20ORDER%2010023&callback=https%3A%2F%2Fmyshop.com%2Fpayment%2Fresult&webhook_url=https%3A%2F%2Fmyshop.com%2Fapi%2Fkos-webhook",
  "qr_code_url": "https://img.vietqr.io/image/MB-0123456789-compact2.png?amount=500000&addInfo=PAY%20ORDER%2010023&accountName=CONG%20THANH%20TOAN%20KOS"
}
```

---

## 📡 2. Xử Lý Push Webhook Server-to-Server (Bắt Buộc)

### 📌 A. Nơi Khai Báo Webhook URL Trên Website Bán Hàng:

Bạn có **2 cách** để cài đặt Webhook URL nhận thông báo từ KOS Gateway:

1. **Cách 1 (Khuyên dùng)**: Truyền trực tiếp trong Request Body khi gọi `POST /api/v1/payment/create`:
   ```json
   {
     "order_id": "ORDER_10023",
     "amount": 500000,
     "content": "PAY ORDER 10023",
     "callback_url": "https://myshop.com/payment/result",
     "webhook_url": "https://myshop.com/api/webhooks/kos"
   }
   ```
2. **Cách 2**: Nhập URL vào ô **Callback URL Mặc Định** trong trang Admin Dashboard KOS Gateway (`/admin`). Nếu đơn hàng không có `webhook_url` riêng, Gateway sẽ tự động gửi về URL mặc định này.

---

### 📩 B. Cấu Trúc Dữ Liệu Webhook Nhận Được (JSON Payload):

KOS Gateway sẽ tự động gửi HTTP POST request đến `webhook_url` của bạn đối với mọi sự kiện thay đổi trạng thái đơn hàng.

#### 1. Webhook Thanh Toán Thành Công (`payment.success`)
Được kích hoạt ngay khi hệ thống đọc được Email thông báo số dư từ Ngân hàng (MB Bank, Vietcombank, Techcombank, ACB, VPBank, Timo...):

```json
{
  "event": "payment.success",
  "status": "completed",
  "order_id": "ORDER_10023",
  "reference_id": "ORDER_10023",
  "payment_id": "f8a92b1c-0e8e-4af9-929a-61cb83517d8e",
  "amount": 500000.0,
  "trans_no": "FT24081099882211",
  "description": "Email (notification@mbbank.com.vn): PAY ORDER 10023 MA DON HANG",
  "date": "10/08/2026 09:45:12",
  "timestamp": 1774425742,
  "signature": "a8f92b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8"
}
```

#### 2. Webhook Thanh Toán Thất Bại / Hủy (`payment.failed`)
Được kích hoạt khi đơn hàng bị Hủy bởi người dùng, bị Hủy bởi Admin, hoặc Hết hạn:

```json
{
  "event": "payment.failed",
  "status": "cancelled",
  "order_id": "ORDER_10023",
  "reference_id": "ORDER_10023",
  "payment_id": "f8a92b1c-0e8e-4af9-929a-61cb83517d8e",
  "amount": 500000.0,
  "trans_no": "",
  "description": "Giao dịch bị hủy bởi người dùng",
  "date": "",
  "timestamp": 1774425742,
  "signature": "b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c"
}
```

---

## 🔒 3. Quy Trình Xác Thực Chữ Ký Bảo Mật (Signature Verification)

Để chống kẻ gian gửi request giả mạo, server website bán hàng của bạn **bắt buộc** phải xác minh chữ ký bảo mật được gửi kèm trong Header `X-Webhook-Signature` hoặc thuộc tính `signature` trong body JSON.

### Công Thức Mã Hóa Chữ Ký (SHA-256):
```text
signature = sha256( order_id + payment_id + amount + trans_no + callback_secret )
```
*(Lưu ý: `callback_secret` là chuỗi Secret Token bảo mật khớp với cài đặt trong file `.env` hoặc Admin Dashboard).*

---

## 💻 4. Code Mẫu Xử Lý Webhook Trên Website Bán Hàng (Đa Ngôn Ngữ)

### 🟢 1. Next.js App Router (`app/api/webhooks/kos/route.ts`)
```typescript
import { NextResponse } from 'next/server';
import crypto from 'crypto';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { event, status, order_id, payment_id, amount, trans_no, signature } = body;
    const secret = process.env.CALLBACK_SECRET || 'super-secret-callback-token';

    // 1. Xác thực chữ ký SHA-256
    const rawString = `${order_id}${payment_id}${amount}${trans_no || ''}${secret}`;
    const expectedSignature = crypto.createHash('sha256').update(rawString).digest('hex');

    if (signature !== expectedSignature) {
      console.error('⚠️ Webhook Signature không hợp lệ!');
      return NextResponse.json({ error: 'Invalid signature' }, { status: 400 });
    }

    // 2. Kiểm tra Idempotency (Tránh xử lý 2 lần nếu nhận lại webhook)
    // const order = await db.orders.findUnique({ where: { id: order_id } });
    // if (order.status === 'PAID') return NextResponse.json({ success: true, message: 'Already processed' });

    // 3. Cập nhật trạng thái đơn hàng trong Database bán hàng
    if (event === 'payment.success' && status === 'completed') {
      console.log(`✅ [Next.js Webhook] Đơn hàng ${order_id} đã thanh toán thành công: ${amount} VNĐ`);
      // await db.orders.update({ where: { id: order_id }, data: { status: 'PAID', transNo: trans_no } });
      // await sendEmailConfirmationToCustomer(order_id);
    } else {
      console.log(`❌ [Next.js Webhook] Đơn hàng ${order_id} thất bại hoặc bị hủy.`);
      // await db.orders.update({ where: { id: order_id }, data: { status: 'CANCELLED' } });
    }

    return NextResponse.json({ success: true, message: 'Webhook received successfully' });
  } catch (error: any) {
    console.error('Lỗi xử lý webhook:', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
```

---

### 🟢 2. Node.js / Express (`server.js`)
```javascript
const express = require('express');
const crypto = require('crypto');
const app = express();

app.use(express.json());

app.post('/api/webhooks/kos', (req, res) => {
  const secret = process.env.CALLBACK_SECRET || 'super-secret-callback-token';
  const { event, status, order_id, payment_id, amount, trans_no, signature } = req.body;

  // 1. Kiểm tra chữ ký bảo mật
  const rawString = `${order_id}${payment_id}${amount}${trans_no || ''}${secret}`;
  const expectedSignature = crypto.createHash('sha256').update(rawString).digest('hex');

  if (signature !== expectedSignature) {
    console.error('⚠️ Chữ ký Webhook không khớp!');
    return res.status(400).json({ error: 'Invalid signature' });
  }

  // 2. Cập nhật trạng thái đơn hàng
  if (event === 'payment.success' && status === 'completed') {
    console.log(`🎉 Đơn hàng ${order_id} đã nhận chuyển khoản ${amount}đ!`);
    // updateOrderStatus(order_id, 'PAID');
  } else {
    console.log(`⚠️ Đơn hàng ${order_id} đã bị hủy.`);
    // updateOrderStatus(order_id, 'CANCELLED');
  }

  return res.status(200).json({ success: true });
});
```

---

### 🐘 3. PHP / Laravel (`app/Http/Controllers/PaymentWebhookController.php`)
```php
<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;
use App\Models\Order;

class PaymentWebhookController extends Controller
{
    public function handleWebhook(Request $request)
    {
        $secret = env('CALLBACK_SECRET', 'super-secret-callback-token');
        $payload = $request->all();

        $orderId = $payload['order_id'] ?? '';
        $paymentId = $payload['payment_id'] ?? '';
        $amount = $payload['amount'] ?? 0;
        $transNo = $payload['trans_no'] ?? '';
        $signature = $payload['signature'] ?? '';

        // 1. Xác minh chữ ký SHA-256
        $rawString = $orderId . $paymentId . $amount . $transNo . $secret;
        $expectedSignature = hash('sha256', $rawString);

        if ($signature !== $expectedSignature) {
            Log::error("⚠️ Webhook signature mismatch for order: " . $orderId);
            return response()->json(['error' => 'Invalid signature'], 400);
        }

        // 2. Xử lý logic cập nhật đơn hàng
        $order = Order::where('id', $orderId)->first();
        if ($order) {
            if ($payload['event'] === 'payment.success' && $payload['status'] === 'completed') {
                $order->update([
                    'status' => 'PAID',
                    'transaction_number' => $transNo
                ]);
                Log::info("✅ [Laravel] Đã cập nhật đơn hàng {$orderId} thành PAID");
            } else {
                $order->update(['status' => 'CANCELLED']);
            }
        }

        return response()->json(['success' => true]);
    }
}
```

---

### 🐍 4. Python / FastAPI (`main.py`)
```python
import hashlib
import os
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

@app.post("/api/webhooks/kos")
async def kos_webhook_handler(request: Request):
    payload = await request.json()
    secret = os.environ.get("CALLBACK_SECRET", "super-secret-callback-token")
    
    order_id = payload.get("order_id", "")
    payment_id = payload.get("payment_id", "")
    amount = payload.get("amount", 0)
    trans_no = payload.get("trans_no", "")
    signature = payload.get("signature", "")
    
    # 1. Tính toán chữ ký SHA-256
    raw_str = f"{order_id}{payment_id}{amount}{trans_no or ''}{secret}"
    expected_signature = hashlib.sha256(raw_str.encode()).hexdigest()
    
    if signature != expected_signature:
        raise HTTPException(status_code=400, detail="Invalid signature")
        
    # 2. Xử lý cập nhật đơn hàng
    if payload.get("event") == "payment.success" and payload.get("status") == "completed":
        print(f"✅ Đơn hàng {order_id} đã thanh toán thành công {amount} VNĐ")
        # update_order_in_db(order_id, "PAID")
    else:
        print(f"❌ Đơn hàng {order_id} đã bị hủy")
        # update_order_in_db(order_id, "CANCELLED")
        
    return {"success": True}
```

---

## 🚫 5. API Hủy Đơn Hàng (`POST /api/v1/payment/cancel`)

Khi người dùng bấm Hủy đơn trên website bán hàng, Backend gọi API này để đánh dấu hủy đơn trên KOS Gateway:

* **Endpoint**: `POST https://your-kos-domain.vercel.app/api/v1/payment/cancel`
* **Request Body**:
  ```json
  {
    "order_id": "ORDER_10023",
    "reason": "Người dùng đổi ý không mua nữa"
  }
  ```

---

## 🔍 6. Kiểm Tra Trạng Thái Chủ Động (`GET /api/check-payment/{order_id}`)

Dành cho frontend polling hoặc nút **"Tôi đã chuyển khoản"**:

* **Request**: `GET https://your-kos-domain.vercel.app/api/check-payment/ORDER_10023`
* **Response**: `{"reference_id": "ORDER_10023", "status": "completed"}` (`pending`, `completed`, `cancelled`, `not_found`).

---

## 💻 6. Ví Dụ Code Tích Hợp Next.js / App Router

### `app/api/checkout/route.ts`
```typescript
import { NextResponse } from 'next/server';

export async function POST(request: Request) {
  const { orderId, amount, content } = await request.json();
  const kosGatewayUrl = process.env.KOS_GATEWAY_URL || 'https://your-kos-gateway.vercel.app';
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000';

  try {
    const res = await fetch(`${kosGatewayUrl}/api/v1/payment/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        order_id: orderId,
        amount: amount,
        content: content,
        callback_url: `${siteUrl}/payment/result`,
        webhook_url: `${siteUrl}/api/webhooks/kos`,
      }),
    });

    const data = await res.json();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json({ error: 'Không thể kết nối cổng thanh toán' }, { status: 500 });
  }
}
```
