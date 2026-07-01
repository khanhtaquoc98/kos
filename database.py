import os
import sqlite3
import time
import logging
import requests
from datetime import datetime

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

# Check if we should use Supabase (REST API)
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

def get_supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

# ----------------- Database Selection Adapter -----------------

def get_sqlite_connection():
    db_path = os.environ.get("SQLITE_DB_PATH")
    if not db_path:
        # Detect Vercel runtime environment
        if os.environ.get("VERCEL") or os.environ.get("NOW_REGION"):
            db_path = "/tmp/db.sqlite"
        else:
            db_path = "db.sqlite"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def execute_sqlite_query(query, params=None, fetch=None):
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
            
        if fetch == 'all':
            columns = [col[0] for col in cursor.description]
            result = [dict(zip(columns, row)) for row in cursor.fetchall()]
        elif fetch == 'one':
            row = cursor.fetchone()
            if row:
                columns = [col[0] for col in cursor.description]
                result = dict(zip(columns, row))
            else:
                result = None
        else:
            conn.commit()
            result = cursor.rowcount
            
        cursor.close()
        return result
    except Exception as e:
        conn.rollback()
        logger.error(f"SQLite error: {e}")
        raise e
    finally:
        conn.close()

# ----------------- Database Initialization -----------------

def init_db():
    """
    Initialize database tables.
    If using Supabase, checks connection and table existence.
    """
    if USE_SUPABASE:
        # Check connection to Supabase REST endpoint
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?limit=1"
        try:
            r = requests.get(url, headers=get_supabase_headers())
            if r.status_code == 200:
                logger.info("Connected to Supabase config table successfully.")
            elif r.status_code == 404:
                logger.error("Supabase config table not found. Please run the SQL schema initialization in the Supabase SQL editor.")
                raise RuntimeError("Supabase config table not found. Please run schema.sql in Supabase SQL Editor.")
            else:
                logger.error(f"Failed to connect to Supabase (HTTP {r.status_code}): {r.text}")
                raise RuntimeError(f"Failed to connect to Supabase: {r.text}")
        except Exception as e:
            logger.error(f"Failed to reach Supabase API: {e}")
            raise e
            
        # Ensure default configs are populated on Supabase
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
    else:
        # Initialize SQLite database
        execute_sqlite_query("""
        CREATE TABLE IF NOT EXISTS config (
            key VARCHAR(255) PRIMARY KEY,
            value TEXT
        )
        """)
        execute_sqlite_query("""
        CREATE TABLE IF NOT EXISTS processed_transactions (
            trans_no VARCHAR(255) PRIMARY KEY,
            amount REAL,
            details TEXT,
            date VARCHAR(255),
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        execute_sqlite_query("""
        CREATE TABLE IF NOT EXISTS pending_payments (
            id VARCHAR(255) PRIMARY KEY,
            reference_id VARCHAR(255),
            amount REAL,
            content TEXT,
            callback_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(50) DEFAULT 'pending'
        )
        """)
        
        # Populate defaults for SQLite
        default_configs = {
            "admin_password": os.environ.get("ADMIN_PASSWORD", "admin123"),
            "mb_username": os.environ.get("MB_USERNAME", ""),
            "mb_password": os.environ.get("MB_PASSWORD", ""),
            "mb_account_number": os.environ.get("MB_ACCOUNT_NUMBER", ""),
            "default_callback_url": os.environ.get("DEFAULT_CALLBACK_URL", ""),
            "callback_secret": os.environ.get("CALLBACK_SECRET", "super-secret-callback-token")
        }
        for key, val in default_configs.items():
            exists = execute_sqlite_query("SELECT key FROM config WHERE key = ?", (key,), fetch='one')
            if not exists:
                execute_sqlite_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, val))

# ----------------- Configuration Helpers -----------------

def get_config(key, default=None):
    if USE_SUPABASE:
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
    else:
        row = execute_sqlite_query("SELECT value FROM config WHERE key = ?", (key,), fetch='one')
        return row['value'] if row else default

def set_config(key, value):
    if USE_SUPABASE:
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
    else:
        exists = execute_sqlite_query("SELECT key FROM config WHERE key = ?", (key,), fetch='one')
        if exists:
            execute_sqlite_query("UPDATE config SET value = ? WHERE key = ?", (value, key))
        else:
            execute_sqlite_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, value))

def get_all_configs():
    if USE_SUPABASE:
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/config?select=key,value"
        try:
            r = requests.get(url, headers=get_supabase_headers())
            if r.status_code == 200:
                return {row["key"]: row["value"] for row in r.json()}
        except Exception as e:
            logger.error(f"Supabase get_all_configs error: {e}")
        return {}
    else:
        rows = execute_sqlite_query("SELECT key, value FROM config", fetch='all')
        return {row['key']: row['value'] for row in rows}

# ----------------- Processed Transactions -----------------

def is_transaction_processed(trans_no):
    if USE_SUPABASE:
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/processed_transactions?trans_no=eq.{trans_no}&select=trans_no"
        try:
            r = requests.get(url, headers=get_supabase_headers())
            if r.status_code == 200:
                return len(r.json()) > 0
        except Exception as e:
            logger.error(f"Supabase is_transaction_processed error: {e}")
        return False
    else:
        row = execute_sqlite_query("SELECT trans_no FROM processed_transactions WHERE trans_no = ?", (trans_no,), fetch='one')
        return row is not None

def add_processed_transaction(trans_no, amount, details, date):
    if USE_SUPABASE:
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
    else:
        try:
            execute_sqlite_query(
                "INSERT INTO processed_transactions (trans_no, amount, details, date) VALUES (?, ?, ?, ?)",
                (trans_no, amount, details, date)
            )
            return True
        except Exception as e:
            logger.warning(f"SQLite processed transaction insert failed: {e}")
            return False

def get_recent_processed_transactions(limit=20):
    if USE_SUPABASE:
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/processed_transactions?order=processed_at.desc&limit={limit}"
        try:
            r = requests.get(url, headers=get_supabase_headers())
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.error(f"Supabase get_recent_processed_transactions error: {e}")
        return []
    else:
        return execute_sqlite_query(
            f"SELECT trans_no, amount, details, date, processed_at FROM processed_transactions ORDER BY processed_at DESC LIMIT ?",
            (limit,),
            fetch='all'
        )

# ----------------- Pending Payments -----------------

def add_pending_payment(payment_id, reference_id, amount, content, callback_url):
    if USE_SUPABASE:
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
    else:
        execute_sqlite_query(
            "INSERT INTO pending_payments (id, reference_id, amount, content, callback_url, status) VALUES (?, ?, ?, ?, ?, 'pending')",
            (payment_id, reference_id, amount, content, callback_url)
        )

def get_pending_payments(limit=50):
    if USE_SUPABASE:
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?order=created_at.desc&limit={limit}"
        try:
            r = requests.get(url, headers=get_supabase_headers())
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.error(f"Supabase get_pending_payments error: {e}")
        return []
    else:
        return execute_sqlite_query(
            "SELECT id, reference_id, amount, content, callback_url, created_at, status FROM pending_payments ORDER BY created_at DESC LIMIT ?",
            (limit,),
            fetch='all'
        )

def update_pending_payment_status(payment_id, status):
    if USE_SUPABASE:
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?id=eq.{payment_id}"
        try:
            requests.patch(url, json={"status": status}, headers=get_supabase_headers())
        except Exception as e:
            logger.error(f"Supabase update_pending_payment_status error: {e}")
    else:
        execute_sqlite_query(
            "UPDATE pending_payments SET status = ? WHERE id = ?",
            (status, payment_id)
        )

def get_pending_payment_status(reference_id):
    if USE_SUPABASE:
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
    else:
        row = execute_sqlite_query(
            "SELECT status FROM pending_payments WHERE reference_id = ? ORDER BY created_at DESC LIMIT 1",
            (reference_id,),
            fetch='one'
        )
        return row['status'] if row else None

def delete_pending_payment(payment_id):
    if USE_SUPABASE:
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?id=eq.{payment_id}"
        try:
            requests.delete(url, headers=get_supabase_headers())
        except Exception as e:
            logger.error(f"Supabase delete_pending_payment error: {e}")
    else:
        execute_sqlite_query(
            "DELETE FROM pending_payments WHERE id = ?",
            (payment_id,)
        )

def delete_all_pending_payments():
    if USE_SUPABASE:
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/pending_payments?status=eq.pending"
        try:
            requests.delete(url, headers=get_supabase_headers())
        except Exception as e:
            logger.error(f"Supabase delete_all_pending_payments error: {e}")
    else:
        execute_sqlite_query(
            "DELETE FROM pending_payments WHERE status = 'pending'"
        )
