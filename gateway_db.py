import os
import time
import logging
import requests

logger = logging.getLogger("mbbank-webhook.database")

# Load .env file if it exists (for local development)
if os.path.exists(".env"):
    try:
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()
    except Exception as e:
        logger.warning(f"Could not load .env file: {e}")

# Supabase Configurations
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

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
            "mb_username": os.environ.get("MB_USERNAME", ""),
            "mb_password": os.environ.get("MB_PASSWORD", ""),
            "mb_account_number": os.environ.get("MB_ACCOUNT_NUMBER", ""),
            "default_callback_url": os.environ.get("DEFAULT_CALLBACK_URL", ""),
            "callback_secret": os.environ.get("CALLBACK_SECRET", "super-secret-callback-token")
        }
        for key, val in default_configs.items():
            if get_config(key) is None:
                set_config(key, val)
    except Exception as e:
        db_initialization_error = f"Error populating default configurations: {e}"

# ----------------- Configuration Helpers -----------------

def get_config(key, default=None):
    if db_initialization_error:
        return default
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?key=eq.{key}&select=value"
    try:
        r = requests.get(url, headers=get_supabase_headers())
        if r.status_code == 200:
            data = r.json()
            if data:
                return data[0]["value"]
    except Exception as e:
        logger.error(f"Supabase get_config error: {e}")
    return default

def set_config(key, value):
    if db_initialization_error:
        return
    # Check if already exists to do PATCH (Update) or POST (Insert)
    exist_url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?key=eq.{key}&select=key"
    try:
        r_exist = requests.get(exist_url, headers=get_supabase_headers())
        if r_exist.status_code == 200 and r_exist.json():
            # Update
            url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?key=eq.{key}"
            requests.patch(url, json={"value": str(value)}, headers=get_supabase_headers())
        else:
            # Insert
            url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config"
            requests.post(url, json={"key": key, "value": str(value)}, headers=get_supabase_headers())
    except Exception as e:
        logger.error(f"Supabase set_config error: {e}")

def get_all_configs():
    if db_initialization_error:
        return {}
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?select=key,value"
    try:
        r = requests.get(url, headers=get_supabase_headers())
        if r.status_code == 200:
            return {row["key"]: row["value"] for row in r.json()}
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

def add_pending_payment(payment_id, reference_id, amount, content, callback_url):
    if db_initialization_error:
        return
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments"
    payload = {
        "id": payment_id,
        "reference_id": reference_id,
        "amount": float(amount),
        "content": content,
        "callback_url": callback_url,
        "status": "pending"
    }
    try:
        requests.post(url, json=payload, headers=get_supabase_headers())
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
