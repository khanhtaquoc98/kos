# 🏦 KOS Webhook Gateway (Google Email API & Multi-Bank Support)

Hệ thống API và Webhook hỗ trợ đối soát giao dịch ngân hàng **tự động 100%** qua Google Email Gateway (Gmail API / IMAP). Hỗ trợ đọc thông báo biến động số dư của tất cả ngân hàng Việt Nam (**MB Bank, Vietcombank, Techcombank, ACB, VPBank, Timo Digital Bank...**). Code được viết bằng Python (FastAPI), tương thích hoàn toàn để deploy lên **Vercel Serverless Functions** & **Supabase Database**.

---

## 📑 Mục lục
1. [Tính Năng Nổi Bật](#1-tính-năng-nổi-bật)
2. [Cấu Trúc Thư Mục](#2-cấu-trúc-thư-mục)
3. [Hướng Dẫn Chạy Local](#3-hướng-dẫn-chạy-local)
4. [Hướng Dẫn Deploy Vercel & Supabase](#4-hướng-dẫn-deploy-vercel--supabase)
5. [Mã Hóa Bảo Mật Thông Tin Nhạy Cảm](#5-mã-hóa-bảo-mật-thông-tin-nhạy-cảm)
6. [Tài Liệu API & Cơ Chế Push Webhook + Callback](#6-tài-liệu-api--cơ-chế-push-webhook--callback)

---

## 1. Tính Năng Nổi Bật

* **Google Email API Gateway & Multi-Bank HTML Parser:** Đọc trực tiếp email thông báo biến động số dư từ Ngân hàng qua **Gmail IMAP App Password** hoặc **Google OAuth2 API**, trích xuất mẫu HTML email và tự động duyệt đơn thanh toán 100% không lo bị captcha/block ngân hàng.
* **Hỗ Trợ Đa Ngân Hàng:** Đã tích hợp sẵn bộ mẫu Regex trích xuất cho **MB Bank, Vietcombank, Techcombank, ACB, VPBank, Timo Digital Bank (BVBank)** và chế độ Generic cho mọi ngân hàng khác.
* **Bộ Đọc HTML Email Mẫu Thông Minh:** Cho phép Admin paste mã HTML email mẫu từ ngân hàng vào Dashboard để test trực tiếp các mẫu Regex trích xuất (Số tiền, Nội dung, Mã GD/Ref, Ngày GD) thời gian thực.
* **Cơ Chế Dual Notification (Webhook + Callback):**
  - **Push Webhook (Server-to-Server):** Bắn HTTP POST bảo mật về `webhook_url` cho cả 2 sự kiện: Thành công (`payment.success`) và Thất bại/Hủy (`payment.failed`).
  - **Browser Callback (Client Redirect):** Điều hướng trình duyệt về `callback_url` kèm tham số `status=completed` hoặc `status=cancelled`.
* **Trang Thanh Toán Checkout PayOS-like (`/checkout`):** Giao diện Dark Mode Glassmorphism cao cấp, đếm ngược 10 phút, tự động tạo VietQR, hiệu ứng pháo hoa Confetti và âm thanh phát Web Audio khi thanh toán thành công.
* **Mã Hóa Bảo Mật Dữ Liệu Config (`enc_v1:`):** Tự động mã hóa AES/XOR-Base64 với SHA-256 secret salt cho các thông tin nhạy cảm (Gmail Pass, OAuth Client Secret, Token, Webhook Secret) lưu trong Supabase Database.
* **Database Linh Hoạt (Supabase REST API):** Lưu trữ cấu hình hệ thống, đơn chờ thanh toán (`pending_payments`) và lịch sử giao dịch đã xử lý (`processed_transactions`).
* **Double-spend Prevention:** Lưu trữ các mã giao dịch đã đối soát (`trans_no`) vào DB để ngăn chặn trùng lặp.

---

## 2. Cấu Trúc Thư Mục

```text
mbbank-webhook/
├── main.py            # FastAPI Entrypoint, API Payment Routes & Cron Handler
├── email_engine.py    # Engine đọc Gmail (IMAP & OAuth2) & HTML Bank Email Parser
├── gateway_db.py      # Logic tương tác Supabase Database REST API & Mã hóa Config
├── schema.sql         # File SQL khởi tạo cơ sở dữ liệu Supabase
├── templates/
│   ├── admin.html     # Dashboard Admin quản lý configs, HTML Email Tester & Logs
│   ├── checkout.html  # Trang thanh toán VietQR PayOS-style cao cấp
│   ├── login.html     # Trang đăng nhập Admin
│   └── demo.html      # Trang demo quét QR code
├── .env.example       # Mẫu cấu hình biến môi trường
├── INTEGRATION.md     # Tài liệu hướng dẫn tích hợp chi tiết cho Developer
├── vercel.json        # File cấu hình deploy Vercel Serverless Function & Cron Job
├── requirements.txt   # Danh sách thư viện Python
└── README.md          # Tài liệu tổng quan hệ thống
```

---

## 3. Hướng Dẫn Chạy Local

Yêu cầu máy đã cài sẵn Python 3.9 trở lên.

1. Di chuyển vào thư mục dự án:
   ```bash
   cd mbbank-webhook
   ```
2. Tạo môi trường ảo (khuyên dùng):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Cài đặt các thư viện cần thiết:
   ```bash
   pip install -r requirements.txt
   ```
4. Chạy ứng dụng bằng Uvicorn:
   ```bash
   python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
   ```
5. Truy cập ứng dụng:
   - **Admin Dashboard**: [http://localhost:8000/admin](http://localhost:8000/admin) *(Mật khẩu mặc định: `admin123`)*
   - **Demo Checkout QR**: [http://localhost:8000/demo](http://localhost:8000/demo)

---

## 4. Hướng Dẫn Deploy Vercel & Supabase

### Bước 1: Chuẩn bị Cơ sở dữ liệu Supabase
1. Đăng nhập [Supabase Dashboard](https://supabase.com) -> Mở dự án -> **SQL Editor**.
2. Copy toàn bộ nội dung file [schema.sql](file:///Users/bo-khanh/Desktop/Src/out/mbbank-webhook/schema.sql), dán vào SQL Editor và nhấn **Run** để khởi tạo các bảng (`config`, `pending_payments`, `processed_transactions`).
3. Vào **Settings > API** để lấy:
   - **Project URL**: `SUPABASE_URL` (ví dụ: `https://xxxx.supabase.co`)
   - **API Key**: `SUPABASE_KEY` (`anon` hoặc `service_role` key)

### Bước 2: Cài đặt biến môi trường trên Vercel
Khi import dự án vào Vercel, hãy cấu hình các **Environment Variables**:
* `SUPABASE_URL`: URL dự án Supabase.
* `SUPABASE_KEY`: Khóa API xác thực Supabase.
* `ENCRYPTION_SECRET`: Mã khóa tùy chọn để mã hóa thông tin nhạy cảm trong Database.
* `CRON_SECRET`: Chuỗi bảo mật dùng cho Vercel Cron Job tự động đọc email đối soát.

### Bước 3: Cấu hình Cron Job trên Vercel (Tự động quét lúc 23:59 hàng ngày)
File `vercel.json` đã được cấu hình sẵn Vercel Cron Job chạy 1 lần/ngày lúc 23:59 (`59 23 * * *`):
```json
{
  "version": 2,
  "builds": [
    {
      "src": "main.py",
      "use": "@vercel/python"
    }
  ],
  "routes": [
    {
      "src": "/(.*)",
      "dest": "main.py"
    }
  ],
  "crons": [
    {
      "path": "/api/cron?secret=YOUR_CRON_SECRET",
      "schedule": "59 23 * * *"
    }
  ]
}
```

---

## 5. Mã Hóa Bảo Mật Thông Tin Nhạy Cảm

Hệ thống tích hợp sẵn cơ chế mã hóa tự động cho các tham số nhạy cảm trong Database (`admin_password`, `gmail_app_password`, `gmail_client_secret`, `gmail_refresh_token`, `callback_secret`):
* Dữ liệu được mã hóa bằng SHA-256 secret salt + XOR cipher + Base64 với tiền tố `enc_v1:`.
* Khi gọi `get_config()`, hệ thống tự động giải mã trả về chuỗi gốc cho ứng dụng.
* Hỗ trợ tương thích ngược hoàn toàn với dữ liệu chưa mã hóa.

---

## 6. Tài Liệu API & Cơ Chế Push Webhook + Callback

Xem chi tiết trong file **[INTEGRATION.md](file:///Users/bo-khanh/Desktop/Src/out/mbbank-webhook/INTEGRATION.md)**.

### A. Tạo Đơn Hàng Thanh Toán (`POST /api/v1/payment/create`)
Website của bạn gửi request tạo đơn hàng thanh toán vào KOS Gateway:

* **Payload mẫu:**
  ```json
  {
    "order_id": "DH1001",
    "amount": 500000,
    "content": "PAY DH1001",
    "callback_url": "https://myshop.com/checkout/result",
    "webhook_url": "https://myshop.com/api/kos-webhook"
  }
  ```
* **Response mẫu:**
  ```json
  {
    "success": true,
    "status": "pending",
    "order_id": "DH1001",
    "payment_id": "c7a8b981-d102-4bb3-9ef4-d3a5e8c1ab2f",
    "amount": 500000.0,
    "content": "PAY DH1001",
    "checkout_url": "https://kos-gateway.vercel.app/checkout?orderId=DH1001&amount=500000&content=PAY+DH1001...",
    "qr_code_url": "https://img.vietqr.io/image/MB-0123456789-compact2.png?amount=500000&addInfo=PAY+DH1001"
  }
  ```

---

### B. Push Webhook Event (`POST webhook_url` - Server to Server)
KOS Gateway sẽ bắn HTTP POST kèm chữ ký bảo mật SHA-256 (`X-Webhook-Signature`) về `webhook_url` của bạn:

* **1. Khi Thanh Toán Thành Công (`payment.success`):**
  ```json
  {
    "event": "payment.success",
    "status": "completed",
    "order_id": "DH1001",
    "reference_id": "DH1001",
    "payment_id": "c7a8b981-d102-4bb3-9ef4-d3a5e8c1ab2f",
    "amount": 500000.0,
    "trans_no": "FT24081099882211",
    "description": "PAY DH1001",
    "date": "10/08/2026 09:45:12",
    "timestamp": 1774425742,
    "signature": "8a7f92b...e4a821"
  }
  ```

* **2. Khi Thanh Toán Bị Hủy / Thất Bại (`payment.failed`):**
  ```json
  {
    "event": "payment.failed",
    "status": "cancelled",
    "order_id": "DH1001",
    "reference_id": "DH1001",
    "payment_id": "c7a8b981-d102-4bb3-9ef4-d3a5e8c1ab2f",
    "amount": 500000.0,
    "trans_no": "",
    "description": "Giao dịch bị hủy bởi người dùng",
    "date": "",
    "timestamp": 1774425742,
    "signature": "8a7f92b...e4a821"
  }
  ```

---

### C. Hủy Đơn Hàng (`POST /api/v1/payment/cancel`)
* **Request:** `{"order_id": "DH1001", "reason": "Người dùng đổi ý"}`
* **Response:** `{"success": true, "order_id": "DH1001", "status": "cancelled"}`

---

### D. Kiểm Tra Trạng Thái Chủ Động (`GET /api/check-payment/{order_id}`)
* **Request:** `GET /api/check-payment/DH1001`
* **Response:** `{"reference_id": "DH1001", "status": "completed"}` (hoặc `pending`, `cancelled`).
