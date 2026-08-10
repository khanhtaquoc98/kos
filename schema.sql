-- =====================================================================
-- MB BANK WEBHOOK GATEWAY - SUPABASE/POSTGRESQL SCHEMA INITIALIZATION
-- Copy and paste this script directly into the Supabase SQL Editor.
-- =====================================================================

-- 1. Table to store system configurations
CREATE TABLE IF NOT EXISTS config (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL
);

-- 2. Table to store transactions that have already been matched and processed (Double-spend prevention)
CREATE TABLE IF NOT EXISTS processed_transactions (
    trans_no VARCHAR(255) PRIMARY KEY,
    amount REAL NOT NULL,
    details TEXT,
    date VARCHAR(255),
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Table to store QR payment check requests that are actively pending confirmation
CREATE TABLE IF NOT EXISTS pending_payments (
    id VARCHAR(255) PRIMARY KEY,
    reference_id VARCHAR(255) NOT NULL,
    amount REAL NOT NULL,
    content TEXT NOT NULL,
    callback_url TEXT,
    webhook_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(50) DEFAULT 'pending'
);

-- 4. Create Indexes for faster querying
CREATE INDEX IF NOT EXISTS idx_pending_payments_ref ON pending_payments(reference_id);
CREATE INDEX IF NOT EXISTS idx_pending_payments_status ON pending_payments(status);
CREATE INDEX IF NOT EXISTS idx_processed_trans_date ON processed_transactions(processed_at DESC);

-- 5. Insert default configurations (if not already existing)
INSERT INTO config (key, value) VALUES 
('admin_password', 'admin123'),
('mb_account_number', ''),
('mb_account_name', 'CỔNG THANH TOÁN NGÂN HÀNG'),
('default_callback_url', ''),
('callback_secret', 'super-secret-callback-token'),
('email_gateway_active', 'true'),
('email_auth_method', 'imap'),
('gmail_address', ''),
('gmail_app_password', ''),
('gmail_client_id', ''),
('gmail_client_secret', ''),
('gmail_refresh_token', ''),
('email_sender_filter', 'notification@mbbank.com.vn'),
('email_html_template', ''),
('email_parser_regex_amount', '(?:Số tiền|Số tiền GD|Giao dịch|Số tiền tăng|Cộng tài khoản|Số tiền biến động)[:\s]*\+?\s*([\d\.,]+)\s*(?:VND|VNĐ|đ)?'),
('email_parser_regex_content', '(?:Nội dung|Nội dung chuyển khoản|NDCK|Nội dung GD)[:\s]*([^\n<]+)'),
('email_parser_regex_trans_no', '(?:Mã giao dịch|Mã GD|Số FT|Ref No|So FT|Số HĐ)[:\s]*([A-Z0-9\.\-]+)'),
('email_parser_regex_date', '(?:Thời gian|Ngày giao dịch|Ngày GD)[:\s]*([\d\/\:\s\-]+)')
ON CONFLICT (key) DO NOTHING;

-- =====================================================================
-- Verification script (Optional, you can run this to see created tables)
-- =====================================================================
-- SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';
