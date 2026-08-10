import asyncio
import imaplib
import email
from email.header import decode_header
import re
import html
import logging
import base64
import httpx
from urllib.parse import quote
from typing import List, Dict, Any, Optional

logger = logging.getLogger("mbbank-webhook.email")

# Built-in Regex Presets for common Vietnamese Banks
BANK_PRESETS = {
    "mbbank": {
        "name": "MB Bank (Ngân Hàng Quân Đội)",
        "sender": "notification@mbbank.com.vn",
        "regex_amount": r"(?:Số tiền biến động|Số tiền ghi có|Số tiền GD|Số tiền tăng|Cộng tài khoản|Số tiền)[:\s\n]*\+?\s*([\d\.,]+)\s*(?:VND|VNĐ|đ)?",
        "regex_content": r"(?:Nội dung chuyển khoản|Nội dung giao dịch|Nội dung GD|NDCK|Nội dung)[:\s\n]*([^\n<]+)",
        "regex_trans_no": r"(?:Mã giao dịch(?:\s*\([^)]*\))?|Mã GD|Số FT|Ref No|So FT|Số HĐ|Ref)[:\s\n]*([A-Z0-9\.\-]+)",
        "regex_date": r"(?:Thời gian giao dịch|Thời gian GD|Thời gian|Ngày giao dịch|Ngày GD)[:\s\n]*([\d\/\:\s\-]+)"
    },
    "vietcombank": {
        "name": "Vietcombank (VCB)",
        "sender": "vietcombank.com.vn",
        "regex_amount": r"(?:Số tiền ghi có|Số tiền tăng|Số tiền)[:\s\n]*\+?\s*([\d\.,]+)\s*(?:VND|VNĐ|đ)?",
        "regex_content": r"(?:Nội dung thanh toán|Nội dung GD|Nội dung)[:\s\n]*([^\n<]+)",
        "regex_trans_no": r"(?:Mã giao dịch(?:\s*\([^)]*\))?|Mã GD|Ref)[:\s\n]*([A-Z0-9\.\-]+)",
        "regex_date": r"(?:Thời gian GD|Thời gian|Ngày GD)[:\s\n]*([\d\/\:\s\-]+)"
    },
    "techcombank": {
        "name": "Techcombank (TCB)",
        "sender": "techcombank.com.vn",
        "regex_amount": r"(?:Số tiền ghi có|Số tiền tăng|Ghi có|Số tiền)[:\s\n]*\+?\s*([\d\.,]+)\s*(?:VND|VNĐ|đ)?",
        "regex_content": r"(?:Nội dung giao dịch|Nội dung GD|Nội dung)[:\s\n]*([^\n<]+)",
        "regex_trans_no": r"(?:Mã giao dịch(?:\s*\([^)]*\))?|Mã GD|FT)[:\s\n]*([A-Z0-9\.\-]+)",
        "regex_date": r"(?:Thời gian GD|Thời gian|Ngày)[:\s\n]*([\d\/\:\s\-]+)"
    },
    "acb": {
        "name": "ACB (Ngân Hàng Á Châu)",
        "sender": "acb.com.vn",
        "regex_amount": r"(?:Số tiền tăng|Ghi có|Số tiền)[:\s\n]*\+?\s*([\d\.,]+)\s*(?:VND|VNĐ|đ)?",
        "regex_content": r"(?:Nội dung GD|Nội dung)[:\s\n]*([^\n<]+)",
        "regex_trans_no": r"(?:Mã GD|Mã giao dịch|Số bút toán)[:\s\n]*([A-Z0-9\.\-]+)",
        "regex_date": r"(?:Thời gian|Ngày)[:\s\n]*([\d\/\:\s\-]+)"
    },
    "vpbank": {
        "name": "VPBank",
        "sender": "vpbank.com.vn",
        "regex_amount": r"(?:Số tiền ghi có|Số tiền)[:\s\n]*\+?\s*([\d\.,]+)\s*(?:VND|VNĐ|đ)?",
        "regex_content": r"(?:Nội dung giao dịch|Nội dung)[:\s\n]*([^\n<]+)",
        "regex_trans_no": r"(?:Mã giao dịch|Số GD)[:\s\n]*([A-Z0-9\.\-]+)",
        "regex_date": r"(?:Thời gian|Ngày)[:\s\n]*([\d\/\:\s\-]+)"
    },
    "timo": {
        "name": "Timo Digital Bank (BVBank)",
        "sender": "support@timo.vn",
        "regex_amount": r"(?:vừa tăng|tăng|vừa cộng|Số tiền ghi có|Số tiền)[:\s\n]*\+?\s*([\d\.,]+)\s*(?:VND|VNĐ|đ)?",
        "regex_content": r"(?:Mô tả|Nội dung chuyển khoản|Nội dung GD|Nội dung)[:\s\n]*([^\n<]+)",
        "regex_trans_no": r"(?:Mã giao dịch(?:\s*\([^)]*\))?|Mã GD|Mô tả)[:\s\n]*([^\n<]+)",
        "regex_date": r"(?:vào|Thời gian|Ngày)[:\s\n]*([\d\/\:\s\-]+)"
    },
    "generic": {
        "name": "Chung (Mọi ngân hàng)",
        "sender": "",
        "regex_amount": r"(?:[\+\s])([\d\.,]{4,15})\s*(?:VND|VNĐ|đ)",
        "regex_content": r"(?:Nội dung|NDCK|Description|ND|Chi tiết)[:\s\n]*([^\n<]+)",
        "regex_trans_no": r"(?:Mã GD|FT|Ref|Mã giao dịch|So FT)[:\s\n]*([A-Z0-9\.\-]+)",
        "regex_date": r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}(?::\d{2})?)"
    }
}

def clean_html_to_text(html_content: str) -> str:
    """Converts HTML content to clean plaintext preserving line breaks and table text."""
    if not html_content:
        return ""
    # Normalize internal newlines to spaces first so table cells stay on the same line
    text = re.sub(r'[\r\n]+', ' ', html_content)
    # Replace closing cell tags </td> or <th> with spaces
    text = re.sub(r'</(?:td|th)\s*>', '   ', text, flags=re.IGNORECASE)
    # Replace <br>, </p>, </tr>, </div> with newlines
    text = re.sub(r'<(?:br|/p|/tr|/div)\s*/?>', '\n', text, flags=re.IGNORECASE)
    # Remove all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities (&nbsp;, &amp;, etc.)
    text = html.unescape(text)
    # Clean up whitespace line by line
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)

def parse_bank_email_html(
    html_content: str,
    regex_amount: Optional[str] = None,
    regex_content: Optional[str] = None,
    regex_trans_no: Optional[str] = None,
    regex_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Parses bank email HTML content and extracts transaction details.
    Returns dict with amount (float), content (str), trans_no (str), date (str), raw_text (str).
    """
    plain_text = clean_html_to_text(html_content)
    
    # Use default MB Bank patterns if custom ones are empty
    r_amt = (regex_amount or "").strip() or BANK_PRESETS["mbbank"]["regex_amount"]
    r_cnt = (regex_content or "").strip() or BANK_PRESETS["mbbank"]["regex_content"]
    r_trn = (regex_trans_no or "").strip() or BANK_PRESETS["mbbank"]["regex_trans_no"]
    r_dat = (regex_date or "").strip() or BANK_PRESETS["mbbank"]["regex_date"]

    amount = 0.0
    content = ""
    trans_no = ""
    trans_date = ""

    # Parse Amount
    try:
        amt_match = re.search(r_amt, plain_text, re.IGNORECASE)
        if amt_match:
            raw_amt_str = amt_match.group(1).replace(".", "").replace(",", "").strip()
            amount = float(raw_amt_str)
    except Exception as e:
        logger.warning(f"Error parsing amount from email text with regex '{r_amt}': {e}")

    # Fallback Amount parser if primary regex failed
    if amount <= 0:
        try:
            fallback_match = re.search(r"(?:\+|\+VND|\+VNĐ|Cộng|tăng)\s*([\d\.,]{4,15})", plain_text, re.IGNORECASE)
            if fallback_match:
                raw_amt_str = fallback_match.group(1).replace(".", "").replace(",", "").strip()
                amount = float(raw_amt_str)
        except Exception:
            pass

    # Parse Content / Description
    try:
        cnt_match = re.search(r_cnt, plain_text, re.IGNORECASE)
        if cnt_match:
            content = cnt_match.group(1).strip()
    except Exception as e:
        logger.warning(f"Error parsing content from email text with regex '{r_cnt}': {e}")

    # Parse Trans No
    try:
        trn_match = re.search(r_trn, plain_text, re.IGNORECASE)
        if trn_match:
            trans_no = trn_match.group(1).strip()
    except Exception as e:
        logger.warning(f"Error parsing trans_no from email text with regex '{r_trn}': {e}")

    # Parse Date
    try:
        dat_match = re.search(r_dat, plain_text, re.IGNORECASE)
        if dat_match:
            trans_date = dat_match.group(1).strip()
    except Exception as e:
        logger.warning(f"Error parsing date from email text with regex '{r_dat}': {e}")

    return {
        "amount": amount,
        "content": content,
        "trans_no": trans_no,
        "date": trans_date,
        "raw_text": plain_text
    }

def fetch_emails_via_imap(
    gmail_address: str,
    app_password: str,
    sender_filter: Optional[str] = None,
    max_emails: int = 15,
    label_filter: Optional[str] = "BankNotify"
) -> List[Dict[str, Any]]:
    """Fetches recent emails via Gmail IMAP SSL connection."""
    if not gmail_address or not app_password:
        raise ValueError("Chưa điền Gmail Address hoặc App Password (Mật khẩu ứng dụng 16 ký tự).")

    clean_app_pass = app_password.replace(" ", "").strip()
    clean_address = gmail_address.strip()
    
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(clean_address, clean_app_pass)
    except Exception as e:
        raise ValueError(f"Lỗi đăng nhập Gmail IMAP. Vui lòng kiểm tra địa chỉ Gmail & Mật khẩu ứng dụng: {e}")

    try:
        folder = (label_filter or "BankNotify").split(",")[0].strip()
        status_sel, _ = mail.select(f'"{folder}"') if folder.upper() != "INBOX" else ("NO", None)
        if status_sel != "OK":
            status_sel, _ = mail.select(folder)
        if status_sel != "OK":
            mail.select("INBOX")
        
        status, messages = mail.search(None, "ALL")
        if status != "OK" or not messages or not messages[0]:
            mail.logout()
            return []

        mail_ids = messages[0].split()
        selected_ids = mail_ids[-max_emails:]
        selected_ids.reverse()

        results = []
        for m_id in selected_ids:
            status, msg_data = mail.fetch(m_id, "(RFC822)")
            if status != "OK" or not msg_data:
                continue
            
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject, encoding = decode_header(msg.get("Subject", ""))[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding or "utf-8", errors="ignore")
                    
                    from_hdr, encoding = decode_header(msg.get("From", ""))[0]
                    if isinstance(from_hdr, bytes):
                        from_hdr = from_hdr.decode(encoding or "utf-8", errors="ignore")

                    msg_id = (msg.get("Message-ID", "") or "").strip() or f"imap_{m_id.decode()}"

                    # Check sender filter
                    if sender_filter:
                        senders = [s.strip().lower() for s in sender_filter.split(",") if s.strip()]
                        if senders and not any(s in from_hdr.lower() for s in senders):
                            continue

                    html_body = ""
                    text_body = ""
                    
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))
                            
                            if "attachment" not in content_disposition:
                                if content_type == "text/html":
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        charset = part.get_content_charset() or "utf-8"
                                        html_body = payload.decode(charset, errors="ignore")
                                elif content_type == "text/plain":
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        charset = part.get_content_charset() or "utf-8"
                                        text_body = payload.decode(charset, errors="ignore")
                    else:
                        content_type = msg.get_content_type()
                        payload = msg.get_payload(decode=True)
                        if payload:
                            charset = msg.get_content_charset() or "utf-8"
                            if content_type == "text/html":
                                html_body = payload.decode(charset, errors="ignore")
                            else:
                                text_body = payload.decode(charset, errors="ignore")

                    content_to_use = html_body if html_body else text_body
                    if content_to_use:
                        results.append({
                            "msg_id": msg_id,
                            "subject": subject,
                            "from": from_hdr,
                            "html": content_to_use,
                            "date": msg.get("Date", "")
                        })

        mail.logout()
        return results
    except Exception as e:
        logger.error(f"Error reading IMAP inbox: {e}")
        try:
            mail.logout()
        except Exception:
            pass
        raise e

async def fetch_emails_via_oauth2(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    sender_filter: Optional[str] = None,
    max_emails: int = 15,
    label_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Fetches recent emails via Google OAuth2 & Gmail API v1."""
    c_id = (client_id or "").strip()
    c_sec = (client_secret or "").strip()
    r_tok = (refresh_token or "").strip()
    
    if not c_id or not c_sec or not r_tok:
        raise ValueError("Chưa điền Client ID, Client Secret hoặc Refresh Token của Google OAuth2.")

    req_timeout = httpx.Timeout(30.0, connect=10.0, read=30.0)
    async with httpx.AsyncClient(timeout=req_timeout) as client:
        # 1. Refresh Access Token with retries for transient network/read timeouts
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "client_id": c_id,
            "client_secret": c_sec,
            "refresh_token": r_tok,
            "grant_type": "refresh_token"
        }
        res = None
        for attempt in range(3):
            try:
                res = await client.post(token_url, data=token_data)
                if res.status_code == 200:
                    break
            except (httpx.TimeoutException, httpx.NetworkError) as te:
                if attempt == 2:
                    raise ValueError(f"Kết nối tới Google OAuth2 token endpoint bị timeout: {te}")
                await asyncio.sleep(0.5)

        if not res or res.status_code != 200:
            err_text = res.text if res else "No response"
            raise ValueError(f"Không thể cấp lại Access Token từ Google OAuth2: {err_text}")

        access_token = res.json().get("access_token")
        headers = {"Authorization": f"Bearer {access_token}"}

        # 2. Search Messages
        q_parts = []
        if label_filter and label_filter.strip().upper() != "ALL":
            labels = [l.strip() for l in label_filter.split(",") if l.strip()]
            if "INBOX" not in [l.upper() for l in labels]:
                labels.append("INBOX")
            if len(labels) == 1:
                q_parts.append(f"label:{labels[0]}")
            elif len(labels) > 1:
                q_parts.append(f"({' OR '.join([f'label:{l}' for l in labels])})")
        elif label_filter is None:
            q_parts.append("(label:BankNotify OR label:INBOX)")

        if sender_filter:
            senders = [s.strip() for s in sender_filter.split(",") if s.strip()]
            if senders:
                q_parts.append(f"from:({' OR '.join(senders)})")
        
        q = " ".join(q_parts)
        list_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults={max_emails}&q={quote(q)}"
        list_res = await client.get(list_url, headers=headers)
        if list_res.status_code != 200:
            raise ValueError(f"Lỗi truy vấn danh sách email từ Gmail API: {list_res.text}")

        messages_data = list_res.json().get("messages", [])
        if not messages_data:
            return []

        # 3. Fetch message payload
        results = []
        for m_item in messages_data:
            m_id = m_item["id"]
            msg_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m_id}?format=full"
            msg_res = await client.get(msg_url, headers=headers)
            if msg_res.status_code != 200:
                continue

            msg_json = msg_res.json()
            payload = msg_json.get("payload", {})
            headers_list = payload.get("headers") or []
            
            subject = next((h.get("value", "") for h in headers_list if isinstance(h, dict) and h.get("name", "").lower() == "subject"), "")
            from_hdr = next((h.get("value", "") for h in headers_list if isinstance(h, dict) and h.get("name", "").lower() == "from"), "")
            date_hdr = next((h.get("value", "") for h in headers_list if isinstance(h, dict) and h.get("name", "").lower() == "date"), "")
            msg_id = next((h.get("value", "") for h in headers_list if isinstance(h, dict) and h.get("name", "").lower() == "message-id"), m_id)

            html_body = ""
            text_body = ""

            def extract_parts(part):
                if not isinstance(part, dict):
                    return
                nonlocal html_body, text_body
                mime_type = part.get("mimeType", "")
                body_dict = part.get("body") or {}
                body_data = body_dict.get("data", "")
                
                if body_data:
                    try:
                        decoded_bytes = base64.urlsafe_b64decode(body_data.encode('ASCII'))
                        text_str = decoded_bytes.decode('utf-8', errors='ignore')
                        if mime_type == "text/html":
                            html_body = text_str
                        elif mime_type == "text/plain":
                            text_body = text_str
                    except Exception:
                        pass

                parts = part.get("parts") or []
                for subpart in parts:
                    extract_parts(subpart)

            extract_parts(payload)

            content_to_use = html_body if html_body else text_body
            if content_to_use:
                results.append({
                    "msg_id": msg_id,
                    "subject": subject,
                    "from": from_hdr,
                    "html": content_to_use,
                    "date": date_hdr
                })

        return results

async def subscribe_gmail_watch(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    topic_name: str,
    label_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Registers Gmail Push Notifications via Google Pub/Sub topic."""
    c_id = (client_id or "").strip()
    c_sec = (client_secret or "").strip()
    r_tok = (refresh_token or "").strip()
    t_name = (topic_name or "").strip()
    
    if not c_id or not c_sec or not r_tok or not t_name:
        raise ValueError("Chưa điền đủ Client ID, Client Secret, Refresh Token hoặc Pub/Sub Topic Name.")

    req_timeout = httpx.Timeout(30.0, connect=10.0, read=30.0)
    async with httpx.AsyncClient(timeout=req_timeout) as client:
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "client_id": c_id,
            "client_secret": c_sec,
            "refresh_token": r_tok,
            "grant_type": "refresh_token"
        }
        res = None
        for attempt in range(3):
            try:
                res = await client.post(token_url, data=token_data)
                if res.status_code == 200:
                    break
            except (httpx.TimeoutException, httpx.NetworkError) as te:
                if attempt == 2:
                    raise ValueError(f"Kết nối tới Google Token endpoint bị timeout: {te}")
                await asyncio.sleep(0.5)

        if not res or res.status_code != 200:
            err_text = res.text if res else "No response"
            raise ValueError(f"Không thể cấp lại Access Token từ Google: {err_text}")

        access_token = res.json().get("access_token")
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        # Resolve label names (e.g. 'BankNotify') to official Gmail label IDs
        target_labels = label_ids or ["BankNotify"]
        builtin_labels = {"INBOX", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT", "SENT", "DRAFT"}
        resolved_label_ids = []
        
        if any(l not in builtin_labels for l in target_labels):
            try:
                labels_url = "https://gmail.googleapis.com/gmail/v1/users/me/labels"
                l_res = await client.get(labels_url, headers=headers)
                if l_res.status_code == 200:
                    user_labels = l_res.json().get("labels", [])
                    label_map = {lbl["name"].lower(): lbl["id"] for lbl in user_labels}
                    label_map.update({lbl["id"].lower(): lbl["id"] for lbl in user_labels})
                    
                    for l_name in target_labels:
                        found_id = label_map.get(l_name.lower())
                        if found_id:
                            resolved_label_ids.append(found_id)
                        else:
                            resolved_label_ids.append(l_name)
                else:
                    resolved_label_ids = target_labels
            except Exception as ex:
                logger.warning(f"Could not resolve label IDs from Gmail API: {ex}")
                resolved_label_ids = target_labels
        else:
            resolved_label_ids = target_labels

        watch_url = "https://gmail.googleapis.com/gmail/v1/users/me/watch"
        body = {
            "topicName": t_name,
            "labelIds": resolved_label_ids
        }
        
        watch_res = await client.post(watch_url, headers=headers, json=body)
        if watch_res.status_code != 200:
            raise ValueError(f"Lỗi kích hoạt Gmail Watch Push: {watch_res.text}")
            
        res_data = watch_res.json()
        res_data["resolved_label_ids"] = resolved_label_ids
        return res_data


