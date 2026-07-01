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
('mb_username', ''),
('mb_password', ''),
('mb_account_number', ''),
('default_callback_url', ''),
('callback_secret', 'super-secret-callback-token')
ON CONFLICT (key) DO NOTHING;

-- =====================================================================
-- Verification script (Optional, you can run this to see created tables)
-- =====================================================================
-- SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';
