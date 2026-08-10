import os
import time
import logging
import requests
import base64
import hashlib

logger = logging.getLogger("mbbank-webhook.database")

# Load .env file automatically if present
def load_env_file():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

load_env_file()

SENSITIVE_CONFIG_KEYS = {
    "admin_password",
    "gmail_app_password",
    "gmail_client_secret",
    "gmail_refresh_token",
    "callback_secret"
}

def _derive_key(key_name: str) -> bytes:
    """Derives a byte key for the specific config key using SHA-256."""
    salt = os.environ.get("ENCRYPTION_SECRET") or os.environ.get("SUPABASE_KEY") or os.environ.get("CALLBACK_SECRET") or "kos-secret-key-salt-2026"
    combined = f"{salt}:{key_name}".encode("utf-8")
    return hashlib.sha256(combined).digest()


def encrypt_val(key_name: str, val: str) -> str:
    """Encrypts a sensitive string value into a base64 encoded string prefixed with 'enc_v1:'."""
    if not val or not isinstance(val, str):
        return val
    if val.startswith("enc_v1:"):
        return val
    
    key = _derive_key(key_name)
    val_bytes = val.encode("utf-8")
    cipher_bytes = bytearray()
    for i, b in enumerate(val_bytes):
        k_byte = key[i % len(key)]
        cipher_bytes.append(b ^ k_byte)
    
    encoded = base64.b64encode(cipher_bytes).decode("utf-8")
    return f"enc_v1:{encoded}"

def decrypt_val(key_name: str, val: str) -> str:
    """Decrypts an encrypted string value prefixed with 'enc_v1:'."""
    if not val or not isinstance(val, str):
        return val
    if not val.startswith("enc_v1:"):
        return val
    
    try:
        raw_b64 = val[7:]
        cipher_bytes = base64.b64decode(raw_b64)
        key = _derive_key(key_name)
        plain_bytes = bytearray()
        for i, b in enumerate(cipher_bytes):
            k_byte = key[i % len(key)]
            plain_bytes.append(b ^ k_byte)
        return plain_bytes.decode("utf-8")
    except Exception as e:
        logger.warning(f"Failed to decrypt config value for {key_name}: {e}")
        return val

# Load .env file if it exists (for local development)
if os.path.exists(".env"):
    try:
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    val = v.strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    os.environ[k.strip()] = val
    except Exception as e:
        logger.warning(f"Could not load .env file: {e}")

# Supabase Configurations
raw_url = os.environ.get("SUPABASE_URL") or ""
raw_key = os.environ.get("SUPABASE_KEY") or ""

SUPABASE_URL = raw_url.strip().strip('"').strip("'")
SUPABASE_KEY = raw_key.strip().strip('"').strip("'")

db_initialization_error = None

if not SUPABASE_URL or not SUPABASE_KEY:
    db_initialization_error = "SUPABASE_URL and SUPABASE_KEY environment variables are missing. Please add them in the Vercel Project Settings."

def get_supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

# ----------------- Database Initialization -----------------

def init_db():
    global db_initialization_error
    if db_initialization_error:
        return

    # Check connection to Supabase REST endpoint
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?limit=1"
    try:
        r = requests.get(url, headers=get_supabase_headers())
        if r.status_code == 200:
            logger.info("Connected to Supabase config table successfully.")
        elif r.status_code == 404:
            err_msg = "Supabase config table not found. Please run the SQL schema initialization in the Supabase SQL editor using schema.sql."
            logger.error(err_msg)
            db_initialization_error = err_msg
            return
        else:
            err_msg = f"Failed to connect to Supabase (HTTP {r.status_code}): {r.text}"
            logger.error(err_msg)
            db_initialization_error = err_msg
            return
    except Exception as e:
        err_msg = f"Failed to reach Supabase API: {e}"
        logger.error(err_msg)
        db_initialization_error = err_msg
        return
        
    # Ensure default configs are populated on Supabase
    try:
        default_configs = {
            "admin_password": os.environ.get("ADMIN_PASSWORD", "admin123"),
            "mb_bank_code": os.environ.get("MB_BANK_CODE", "MB"),
            "mb_account_number": os.environ.get("MB_ACCOUNT_NUMBER", ""),
            "mb_account_name": os.environ.get("MB_ACCOUNT_NAME", "CỔNG THANH TOÁN NGÂN HÀNG"),
            "default_callback_url": os.environ.get("DEFAULT_CALLBACK_URL", ""),
            "callback_secret": os.environ.get("CALLBACK_SECRET", "super-secret-callback-token"),
            "mb_system_active": "true",
            "bank_scan_interval": "30",
            "email_gateway_active": "true",
            "email_auth_method": "imap",
            "gmail_address": os.environ.get("GMAIL_ADDRESS", ""),
            "gmail_app_password": os.environ.get("GMAIL_APP_PASSWORD", ""),
            "gmail_client_id": os.environ.get("GMAIL_CLIENT_ID", ""),
            "gmail_client_secret": os.environ.get("GMAIL_CLIENT_SECRET", ""),
            "gmail_refresh_token": os.environ.get("GMAIL_REFRESH_TOKEN", ""),
            "email_sender_filter": "notification@mbbank.com.vn",
            "email_html_template": "",
            "email_parser_regex_amount": r"(?:Số tiền|Số tiền GD|Giao dịch|Số tiền tăng|Cộng tài khoản|Số tiền biến động)[:\s]*\+?\s*([\d\.,]+)\s*(?:VND|VNĐ|đ)?",
            "email_parser_regex_content": r"(?:Nội dung|Nội dung chuyển khoản|NDCK|Nội dung GD)[:\s]*([^\n<]+)",
            "email_parser_regex_trans_no": r"(?:Mã giao dịch|Mã GD|Số FT|Ref No|So FT|Số HĐ)[:\s]*([A-Z0-9\.\-]+)",
            "email_parser_regex_date": r"(?:Thời gian|Ngày giao dịch|Ngày GD)[:\s]*([\d\/\:\s\-]+)"
        }
        for key, val in default_configs.items():
            if get_config(key) is None:
                set_config(key, val)
        
        # Always synchronize ADMIN_PASSWORD and Google Client ID/Secret if explicitly set in environment
        env_admin_pass = os.environ.get("ADMIN_PASSWORD")
        if env_admin_pass:
            set_config("admin_password", env_admin_pass)
            
        env_client_id = os.environ.get("GMAIL_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID")
        if env_client_id:
            set_config("gmail_client_id", env_client_id)
            
        env_client_secret = os.environ.get("GMAIL_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET")
        if env_client_secret:
            set_config("gmail_client_secret", env_client_secret)
    except Exception as e:
        db_initialization_error = f"Error populating default configurations: {e}"

# ----------------- Configuration Helpers -----------------

ENV_KEYS_MAP = {
    "admin_password": "ADMIN_PASSWORD",
    "callback_secret": "CALLBACK_SECRET",
    "default_callback_url": "DEFAULT_CALLBACK_URL",
    "mb_bank_code": "MB_BANK_CODE",
    "mb_account_number": "MB_ACCOUNT_NUMBER",
    "mb_account_name": "MB_ACCOUNT_NAME",
    "gmail_address": "GMAIL_ADDRESS",
    "gmail_app_password": "GMAIL_APP_PASSWORD",
    "gmail_client_id": "GMAIL_CLIENT_ID",
    "gmail_client_secret": "GMAIL_CLIENT_SECRET",
    "gmail_refresh_token": "GMAIL_REFRESH_TOKEN",
    "email_gateway_active": "EMAIL_GATEWAY_ACTIVE",
    "email_auth_method": "EMAIL_AUTH_METHOD",
    "email_sender_filter": "EMAIL_SENDER_FILTER",
    "email_html_template": "EMAIL_HTML_TEMPLATE",
    "email_parser_regex_amount": "EMAIL_PARSER_REGEX_AMOUNT",
    "email_parser_regex_content": "EMAIL_PARSER_REGEX_CONTENT",
    "email_parser_regex_trans_no": "EMAIL_PARSER_REGEX_TRANS_NO",
    "email_parser_regex_date": "EMAIL_PARSER_REGEX_DATE",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "telegram_notify_active": "TELEGRAM_NOTIFY_ACTIVE",
}

_config_cache = {}
_cache_timestamp = 0.0
CONFIG_CACHE_TTL = 60.0  # seconds

def _refresh_config_cache_if_needed():
    global _config_cache, _cache_timestamp
    now = time.time()
    if not _config_cache or (now - _cache_timestamp > CONFIG_CACHE_TTL):
        all_cfgs = get_all_configs()
        if all_cfgs:
            _config_cache = all_cfgs
            _cache_timestamp = now

def get_config(key, default=None):
    if not db_initialization_error:
        _refresh_config_cache_if_needed()
        val = _config_cache.get(key)
        if val is not None and val != "":
            return val

    # Fallback to Environment Variables (.env)
    env_var_name = ENV_KEYS_MAP.get(key)
    if env_var_name:
        env_val = os.environ.get(env_var_name)
        if env_val:
            return env_val
            
    if key == "gmail_client_id":
        alias_val = os.environ.get("GOOGLE_CLIENT_ID") or os.environ.get("NEXT_PUBLIC_GOOGLE_CLIENT_ID")
        if alias_val:
            return alias_val
    if key == "gmail_client_secret":
        alias_val = os.environ.get("GOOGLE_CLIENT_SECRET")
        if alias_val:
            return alias_val

    return default

def set_config(key, value):
    global _config_cache, _cache_timestamp
    if db_initialization_error:
        return
    val_to_save = str(value)
    
    # Update memory cache immediately
    _config_cache[key] = val_to_save
    _cache_timestamp = time.time()

    val_for_db = encrypt_val(key, val_to_save) if key in SENSITIVE_CONFIG_KEYS else val_to_save

    # Check if already exists to do PATCH (Update) or POST (Insert)
    exist_url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?key=eq.{key}&select=key"
    try:
        r_exist = requests.get(exist_url, headers=get_supabase_headers())
        if r_exist.status_code == 200 and r_exist.json():
            # Update
            url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?key=eq.{key}"
            requests.patch(url, json={"value": val_for_db}, headers=get_supabase_headers())
        else:
            # Insert
            url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config"
            requests.post(url, json={"key": key, "value": val_for_db}, headers=get_supabase_headers())
    except Exception as e:
        logger.error(f"Supabase set_config error: {e}")

def get_all_configs():
    if db_initialization_error:
        return {}
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?select=key,value"
    try:
        r = requests.get(url, headers=get_supabase_headers())
        if r.status_code == 200:
            res = {}
            for row in r.json():
                k = row["key"]
                v = row["value"]
                if k in SENSITIVE_CONFIG_KEYS:
                    v = decrypt_val(k, v)
                res[k] = v
            return res
    except Exception as e:
        logger.error(f"Supabase get_all_configs error: {e}")
    return {}

# ----------------- Processed Transactions -----------------

def is_transaction_processed(trans_no):
    if db_initialization_error:
        return False
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/processed_transactions?trans_no=eq.{trans_no}&select=trans_no"
    try:
        r = requests.get(url, headers=get_supabase_headers())
        if r.status_code == 200:
            return len(r.json()) > 0
    except Exception as e:
        logger.error(f"Supabase is_transaction_processed error: {e}")
    return False

def add_processed_transaction(trans_no, amount, details, date):
    if db_initialization_error:
        return False
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/processed_transactions"
    payload = {
        "trans_no": trans_no,
        "amount": float(amount),
        "details": details,
        "date": date
    }
    try:
        r = requests.post(url, json=payload, headers=get_supabase_headers())
        return r.status_code in [200, 201]
    except Exception as e:
        logger.error(f"Supabase add_processed_transaction error: {e}")
        return False

def get_recent_processed_transactions(limit=20):
    if db_initialization_error:
        return []
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/processed_transactions?order=processed_at.desc&limit={limit}"
    try:
        r = requests.get(url, headers=get_supabase_headers())
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"Supabase get_recent_processed_transactions error: {e}")
    return []

# ----------------- Pending Payments -----------------

def add_pending_payment(payment_id, reference_id, amount, content, callback_url, webhook_url=""):
    if db_initialization_error:
        return
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments"
    payload = {
        "id": payment_id,
        "reference_id": reference_id,
        "amount": float(amount),
        "content": content,
        "callback_url": callback_url or "",
        "webhook_url": webhook_url or "",
        "status": "pending"
    }
    try:
        r = requests.post(url, json=payload, headers=get_supabase_headers())
        if r.status_code not in [200, 201]:
            # Fallback for legacy database schema if webhook_url column does not exist yet
            payload_legacy = {
                "id": payment_id,
                "reference_id": reference_id,
                "amount": float(amount),
                "content": content,
                "callback_url": callback_url or webhook_url or "",
                "status": "pending"
            }
            requests.post(url, json=payload_legacy, headers=get_supabase_headers())
    except Exception as e:
        logger.error(f"Supabase add_pending_payment error: {e}")

def get_pending_payments(limit=50):
    if db_initialization_error:
        return []
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?order=created_at.desc&limit={limit}"
    try:
        r = requests.get(url, headers=get_supabase_headers())
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"Supabase get_pending_payments error: {e}")
    return []

def update_pending_payment_status(payment_id, status):
    if db_initialization_error:
        return
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?id=eq.{payment_id}"
    try:
        requests.patch(url, json={"status": status}, headers=get_supabase_headers())
    except Exception as e:
        logger.error(f"Supabase update_pending_payment_status error: {e}")

def get_pending_payment_status(reference_id):
    if db_initialization_error:
        return None
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?reference_id=eq.{reference_id}&select=status&order=created_at.desc&limit=1"
    try:
        r = requests.get(url, headers=get_supabase_headers())
        if r.status_code == 200:
            data = r.json()
            if data:
                return data[0]["status"]
    except Exception as e:
        logger.error(f"Supabase get_pending_payment_status error: {e}")
    return None

def get_pending_payment_by_ref(reference_id):
    if db_initialization_error:
        return None
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?reference_id=eq.{reference_id}&order=created_at.desc&limit=1"
    try:
        r = requests.get(url, headers=get_supabase_headers())
        if r.status_code == 200:
            data = r.json()
            if data:
                return data[0]
    except Exception as e:
        logger.error(f"Supabase get_pending_payment_by_ref error: {e}")
    return None

def delete_pending_payment(payment_id):
    if db_initialization_error:
        return
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?id=eq.{payment_id}"
    try:
        requests.delete(url, headers=get_supabase_headers())
    except Exception as e:
        logger.error(f"Supabase delete_pending_payment error: {e}")

def delete_all_pending_payments():
    if db_initialization_error:
        return
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?status=eq.pending"
    try:
        requests.delete(url, headers=get_supabase_headers())
    except Exception as e:
        logger.error(f"Supabase delete_all_pending_payments error: {e}")
