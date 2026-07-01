# Hướng Dẫn Tích Hợp Cổng Thanh Toán MB Bank Webhook Gateway

Hệ thống đã hỗ trợ trang thanh toán `/checkout` mới với giao diện Dark Mode Glassmorphism cao cấp, mô phỏng trải nghiệm thanh toán của PayOS.

---

## 🚀 Tính Năng Nổi Bật
1. **Giao diện hiện đại (PayOS-like):** Thiết kế tối ưu sự tập trung vào mã QR, tích hợp hiệu ứng tia laser quét liên tục, nút sao chép thông tin nhanh, và hỗ trợ hoàn toàn responsive (Mobile & Desktop).
2. **Đăng ký tự động:** Khi người dùng truy cập trang, hệ thống sẽ tự động lưu thông tin giao dịch vào bảng `pending_payments` (sqlite/supabase) để tiến hành quét đối soát.
3. **Bộ đếm thời gian (Countdown):** Đồng hồ đếm ngược 10 phút dạng SVG hình tròn sang trọng. Tự động chuyển màu cảnh báo khi sắp hết giờ.
4. **Xác nhận & Chuyển hướng tự động:** Tự động gọi API `/api/check-payment/{orderId}` để kiểm tra trạng thái ngân hàng mỗi 4 giây. Khi phát hiện đã chuyển khoản thành công:
   - Phát âm thanh báo thành công (chime melody) được tổng hợp qua Web Audio API.
   - Hiển thị hiệu ứng pháo hoa Confetti và màn hình thông báo thành công.
   - Tự động chuyển hướng trình duyệt về link `callback` sau 3 giây.
5. **Huỷ giao dịch:** Cho phép người dùng chủ động bấm huỷ và tự động redirect về trang `cancel_url` với các tham số tương ứng để website bán hàng xử lý trạng thái huỷ.

---

## 🛠️ Danh Sách Tham Số Của Cổng `/checkout`

Khi gọi trang `/checkout` (phương thức `GET`), hãy truyền các tham số truy vấn (query parameters) sau:

| Tham số | Loại | Bắt buộc | Mô tả |
| :--- | :--- | :--- | :--- |
| `amount` | `number` | **Có** | Số tiền thanh toán (VND) |
| `content` | `string` | **Có** | Nội dung chuyển khoản cần khớp (không dấu, ví dụ: `PaidLunch user123`) |
| `orderId` | `string` | **Có** | ID đơn hàng từ database của bạn (dùng làm reference_id để check trạng thái) |
| `callback` | `string` | **Có** | URL chuyển hướng trình duyệt khi thanh toán thành công (Ví dụ: `http://localhost:3000/payment/result`) |
| `orderCode` | `string` | Không | Mã số đơn hàng hiển thị (Ví dụ: `123456`) |
| `cancel_url` | `string` | Không | URL chuyển hướng khi bấm **Huỷ**. Nếu để trống, hệ thống sẽ dùng `callback` và tự động gắn thêm `?status=cancelled&cancel=true` |
| `webhook_url`| `string` | Không | URL nhận thông báo webhook backend-to-backend (Nếu trống sẽ dùng `default_callback_url` cấu hình ở Admin Dashboard) |

---

## 💻 Hướng Dẫn Tích Hợp Vào Ứng Dụng Football

Trong file [checkout/route.ts](file:///Users/bo-khanh/Desktop/Src/out/football/src/app/api/payment/checkout/route.ts), thay vì gọi SDK PayOS để lấy link thanh toán, bạn chỉ cần sinh ra link dẫn tới cổng gateway mới của mình.

### Trước (Dùng PayOS):
```typescript
const paymentLink = await payos.paymentRequests.create({
  orderCode,
  amount: totalAmount,
  description,
  items,
  returnUrl: `${baseUrl}/payment/result?orderCode=${orderCode}&orderId=${orderData.id}`,
  cancelUrl: `${baseUrl}/payment/result?orderCode=${orderCode}&orderId=${orderData.id}&status=cancelled`,
});

return NextResponse.json({
  checkoutUrl: paymentLink.checkoutUrl,
  orderCode,
  orderId: orderData.id,
});
```

### Sau (Dùng MB Bank Webhook Gateway):
Giả sử tên miền cổng gateway của bạn là `http://localhost:8000` (hoặc domain vercel của bạn):

```typescript
const gatewayBaseUrl = process.env.GATEWAY_URL || 'http://localhost:8000';

// Tạo link chuyển hướng trực tiếp
const checkoutUrl = `${gatewayBaseUrl}/checkout` +
  `?amount=${totalAmount}` +
  `&content=${encodeURIComponent(description)}` +
  `&orderId=${orderData.id}` +
  `&orderCode=${orderCode}` +
  `&callback=${encodeURIComponent(`${baseUrl}/payment/result`)}` +
  `&cancel_url=${encodeURIComponent(`${baseUrl}/payment/result?orderCode=${orderCode}&orderId=${orderData.id}&status=cancelled`)}`;

return NextResponse.json({
  checkoutUrl,
  orderCode,
  orderId: orderData.id,
});
```

---

## 🔄 Luồng Hoạt Động Khớp Nối Callback

1. **Khách hàng** bấm thanh toán trên web Football -> Server gọi API tạo đơn -> Trả về `checkoutUrl` trỏ tới Gateway.
2. **Khách hàng** được dẫn tới trang `http://gateway/checkout?amount=...` của Gateway.
3. Trên trang Gateway, mã QR được quét và kiểm tra trạng thái ngân hàng thực tế qua `/api/check-payment/{orderId}`.
4. Khi **Khách hàng chuyển khoản thành công**:
   - Cổng Gateway ghi nhận qua lịch sử giao dịch MB Bank -> Cập nhật trạng thái `completed` trong database.
   - Gateway tự động bắn webhook POST chứa chữ ký bảo mật tới backend của Football app (`webhook_url` hoặc `default_callback_url`) để cập nhật trạng thái đơn hàng trong DB Football sang `paid`.
   - Trình duyệt trên trang Gateway nhận thấy trạng thái đã `completed` -> Tự động chuyển hướng khách hàng về `callback` (là trang `/payment/result` của Football app).
5. Trang kết quả `/payment/result` của Football app tải lên -> Tự động fetch trạng thái đơn hàng từ database của Football app -> Hiển thị thông báo **Thanh toán thành công!** như thiết kế ban đầu.
