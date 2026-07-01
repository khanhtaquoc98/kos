# 🏦 MB Bank Webhook Gateway (Vercel Deployment Ready)

Hệ thống API và Webhook hỗ trợ đối soát giao dịch ngân hàng **MB Bank** tự động. Code được viết bằng Python (FastAPI), tương thích hoàn toàn để deploy lên **Vercel Serverless Functions**.

## 📑 Mục lục
1. [Xác nhận thư viện (mbbank-lib)](#1-xác-nhận-thư-viện-mbbank-lib)
2. [Tính năng nổi bật](#2-tính-năng-nổi-bật)
3. [Cấu trúc thư mục](#3-cấu-trúc-thư-mục)
4. [Hướng dẫn chạy Local](#4-hướng-dẫn-chạy-local)
5. [Hướng dẫn deploy Vercel](#5-hướng-dẫn-deploy-vercel)
6. [Tài liệu API & Xác thực Webhook](#6-tài-liệu-api--xác-thực-webhook)

---

## 1. Xác nhận thư viện (mbbank-lib)
Thư viện `mbbank-lib` **hoàn toàn có thể xử lý tốt** yêu cầu này.
* **Xác thực tự động:** Thư viện sử dụng cơ chế đăng nhập trực tiếp qua API Internet Banking cá nhân.
* **Tự động giải mã CAPTCHA:** Tích hợp mô hình OCR AI (`mb-capcha-ocr` sử dụng ONNX Runtime) để tự động giải CAPTCHA tại local/serverless mà không cần gọi API giải captcha trả phí ngoài.
* **Cơ chế Retry thông minh:** Khi đăng nhập, nếu CAPTCHA giải sai (mã lỗi `GW283`), thư viện sẽ tự động tải CAPTCHA mới và thử lại (mặc định tối đa 30 lần) cho đến khi đăng nhập thành công.
* **Asynchronous:** Dự án này sử dụng `MBBankAsync` giúp xử lý bất đồng bộ (non-blocking IO) cực kỳ mượt mà trên FastAPI.

---

## 2. Tính năng nổi bật
* **Dashboard Admin (Glassmorphism):** Giao diện quản lý cấu hình cao cấp, hỗ trợ ẩn/hiện mật khẩu, kiểm tra kết nối tài khoản ngân hàng thời gian thực, quét giao dịch thủ công.
* **Database Linh Hoạt:** Tự động phát hiện môi trường. Dùng SQLite file khi chạy local và kết nối PostgreSQL/Supabase khi deploy Vercel.
* **Double-spend Prevention:** Lưu trữ các mã giao dịch đã đối soát (`trans_no`) vào DB để ngăn chặn gọi callback trùng lặp (tránh lỗi cộng tiền 2 lần).
* **Vercel Cron Job:** Endpoint `/api/cron` bảo mật cho phép cấu hình Vercel Cron chạy mỗi phút tự động đối soát giao dịch.
* **Bảo mật Webhook:** Callback gửi đi chứa chữ ký HMAC `X-Webhook-Signature` được tạo từ các thông tin giao dịch kèm mã bảo mật `callback_secret`.

---

## 3. Cấu trúc thư mục
```text
mbbank-webhook/
├── api/
│   └── index.py       # FastAPI Entrypoint cho Vercel
├── templates/
│   ├── login.html     # Giao diện đăng nhập Admin cao cấp
│   └── admin.html     # Dashboard Admin quản lý configs, transactions & logs
├── database.py        # Logic tương tác DB (SQLite / PostgreSQL)
├── requirements.txt   # Các thư viện Python cần thiết
├── vercel.json        # File cấu hình deploy Vercel
└── README.md          # Tài liệu hướng dẫn sử dụng
```

---

## 4. Hướng dẫn chạy Local
Yêu cầu máy cài sẵn Python 3.9 trở lên.

1. Di chuyển vào thư mục dự án:
   ```bash
   cd mbbank-webhook
   ```
2. Tạo môi trường ảo (khuyên dùng):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Cài đặt các dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Chạy ứng dụng locally bằng Uvicorn:
   ```bash
   uvicorn main:app --reload --port 8000
   ```
5. Truy cập `http://localhost:8000/` để đăng nhập dashboard (mật khẩu mặc định: `admin123`).

---

## 5. Hướng dẫn deploy Vercel

Dự án đã được cấu hình sẵn file `vercel.json` để biên dịch chạy Python Serverless Function.

### Bước 1: Chuẩn bị Cơ sở dữ liệu (PostgreSQL/Supabase)
Vì Vercel là Serverless (Stateless - không lưu file tĩnh lâu dài), file `db.sqlite` sẽ bị xóa mỗi khi hàm khởi tạo lại. Bạn cần chuẩn bị một URL PostgreSQL (ví dụ từ Supabase).
* Chuỗi kết nối dạng: `postgresql://postgres:password@db.supabase.co:5432/postgres`

### Bước 2: Cài đặt biến môi trường trên Vercel Dashboard
Khi import dự án vào Vercel, hãy cấu hình các **Environment Variables**:
* `DATABASE_URL`: Đường dẫn kết nối database PostgreSQL (Supabase).
* `CRON_SECRET`: Một chuỗi ngẫu nhiên dùng để xác thực cho Cron Job (ví dụ: `my-super-cron-secret-123`).

### Bước 3: Cấu hình Cron Job trên Vercel (Tự động quét mỗi phút)
Để Vercel tự động gọi quét giao dịch MB Bank mỗi phút, thêm phần cấu hình cron vào file `vercel.json` hoặc thiết lập trên Dashboard Vercel. 
File `vercel.json` mặc định:
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
      "path": "/api/cron?secret=MÃ_BẢO_MẬT_CRON",
      "schedule": "*/1 * * * *"
    }
  ]
}
```
*(Thay thế `MÃ_BẢO_MẬT_CRON` bằng giá trị của biến `CRON_SECRET` đã cài).*

---

## 6. Tài liệu API & Xác thực Webhook

### A. Đăng ký kiểm tra QR Code (`POST /api/webhook/check-qr`)
Khi hệ thống bên ngoài tạo mã QR cho người dùng chuyển khoản, hãy gửi yêu cầu vào API này để đăng ký đối chiếu.

* **Payload mẫu:**
  ```json
  {
    "reference_id": "ORDER_12345",
    "amount": 50000,
    "content": "PaidLunch anhkhang",
    "callback_url": "https://website-cua-ban.com/api/payment-callback"
  }
  ```
* **Response mẫu:**
  ```json
  {
    "success": true,
    "payment_id": "c7a8b981-d102-4bb3-9ef4-d3a5e8c1ab2f",
    "status": "pending",
    "message": "QR registered. System is actively scanning for transaction."
  }
  ```

### B. Callback Webhook gửi về Website bên ngoài (`POST callback_url`)
Khi tìm thấy giao dịch chuyển tiền khớp với `content` và `amount` đã đăng ký, hệ thống sẽ thực hiện gọi callback đến URL đã đăng ký.

* **Payload mẫu:**
  ```json
  {
    "status": "success",
    "reference_id": "ORDER_12345",
    "payment_id": "c7a8b981-d102-4bb3-9ef4-d3a5e8c1ab2f",
    "amount": 50000.0,
    "trans_no": "FT24018239480",
    "description": "PaidLunch anhkhang chuyen khoan",
    "date": "01/07/2026 14:05:22",
    "timestamp": 1782895522,
    "signature": "8a7f92b...e4a821"
  }
  ```
* **Bảo mật & Xác thực Chữ ký (Signature Verification):**
  Để tránh hacker giả mạo callback gửi tiền thành công, server của bạn **phải xác minh** chữ ký gửi kèm trong Header `X-Webhook-Signature` hoặc trường `signature`.
  
  **Cách tạo chữ ký trên Server của bạn để so khớp:**
  1. Lấy chuỗi ký tự theo thứ tự: `reference_id + payment_id + amount + trans_no + callback_secret`
  2. Băm chuỗi trên bằng thuật toán **SHA-256**.
  3. So khớp chuỗi sau khi băm với `signature` được gửi đến. Nếu trùng khớp, giao dịch là hợp lệ.

  *Ví dụ code Python xác thực chữ ký:*
  ```python
  import hashlib
  
  secret = "MÃ_CALLBACK_SECRET_ĐÃ_CẤU_HÌNH"
  data = req.json()
  
  sign_str = f"{data['reference_id']}{data['payment_id']}{data['amount']}{data['trans_no']}{secret}"
  expected_signature = hashlib.sha256(sign_str.encode()).hexdigest()
  
  if data['signature'] == expected_signature:
      # Giao dịch hợp lệ, tiến hành cập nhật đơn hàng
      pass
  ```

### C. Đối soát chủ động không dùng Cron Job (`GET /api/check-payment/{reference_id}`)
Nếu bạn **không muốn sử dụng Cron Job** chạy ngầm liên tục, bạn có thể thiết lập hệ thống kiểm tra chủ động (Active Pulling):

1. **Cách hoạt động:**
   * Khi người dùng ở giao diện thanh toán (đang hiển thị QR code), website của bạn (frontend) có thể gửi request định kỳ (ví dụ: mỗi 5-10 giây) hoặc hiển thị nút **"Tôi đã chuyển khoản"**.
   * Khi người dùng nhấp vào nút hoặc theo chu kỳ polling, frontend gọi đến: `GET /api/check-payment/{reference_id}`.
   * Khi nhận được request này, Gateway sẽ ngay lập tức đăng nhập MB Bank, quét các giao dịch mới nhất trong ngày, đối soát, bắn callback (nếu khớp và chưa xử lý), và trả ngay trạng thái về cho client.

* **Response mẫu (Khi chưa thanh toán):**
  ```json
  {
    "reference_id": "ORDER_12345",
    "status": "pending"
  }
  ```
* **Response mẫu (Khi đã thanh toán thành công):**
  ```json
  {
    "reference_id": "ORDER_12345",
    "status": "completed"
  }
  ```
  *(Khi chuyển sang `completed`, đồng thời webhook callback ở phần B cũng đã được tự động kích hoạt gửi về backend của bạn).*
