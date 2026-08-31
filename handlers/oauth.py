import os
import logging
from typing import Optional
from urllib.parse import quote
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import requests

import gateway_db

logger = logging.getLogger("mbbank-webhook.oauth")

router = APIRouter(tags=["OAuth2"])

@router.get("/api/oauth2/connect")
async def google_oauth_connect(request: Request, path: Optional[str] = "/api/auth/callback/google"):
    """Initiates Google OAuth2 SSO Authorization flow to connect Gmail account."""
    client_id = (gateway_db.get_config("gmail_client_id") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip().strip('"').strip("'")
    if not client_id:
        return HTMLResponse(content="""
        <script>
            alert("Chưa cấu hình GOOGLE_CLIENT_ID trong file .env!");
            window.location.href = "/admin";
        </script>
        """, status_code=400)

    base_url = str(request.base_url).rstrip('/')
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        base_url = f"{forwarded_proto}://{request.url.netloc}"

    callback_path = path if path.startswith('/') else f"/{path}"
    redirect_uri = f"{base_url}{callback_path}"
    scope = quote("https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/userinfo.email")
    
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={client_id}&"
        f"redirect_uri={quote(redirect_uri)}&"
        f"response_type=code&"
        f"scope={scope}&"
        f"access_type=offline&"
        f"prompt=consent"
    )
    logger.info(f"Initiating Google OAuth connect with redirect_uri: {redirect_uri}")
    return RedirectResponse(url=auth_url)

@router.get("/api/oauth2/callback")
@router.get("/api/auth/callback/google")
async def google_oauth_callback(request: Request, code: Optional[str] = None, error: Optional[str] = None):
    """Handles Google OAuth2 callback code exchange, fetches email and refresh token, and saves to DB."""
    if error or not code:
        logger.error(f"Google OAuth callback error: {error}")
        return RedirectResponse(url=f"/admin?oauth_error={quote(error or 'Người dùng hủy ủy quyền Google')}")
        
    client_id = (gateway_db.get_config("gmail_client_id") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip().strip('"').strip("'")
    client_secret = (gateway_db.get_config("gmail_client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip().strip('"').strip("'")
    
    base_url = str(request.base_url).rstrip('/')
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        base_url = f"{forwarded_proto}://{request.url.netloc}"

    path = request.url.path
    redirect_uri = f"{base_url}{path}"
    
    try:
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"
        }
        
        r = requests.post(token_url, data=token_data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code != 200:
            logger.error(f"Failed token exchange: status={r.status_code}, resp={r.text}")
            return RedirectResponse(url=f"/admin?oauth_error={quote('Lỗi đổi token từ Google API (' + str(r.status_code) + '): ' + r.text)}")
            
        res_data = r.json()
        access_token = res_data.get("access_token")
        refresh_token = res_data.get("refresh_token")
        
        # Get User Email from UserInfo API
        user_info_res = requests.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {access_token}"})
        gmail_address = ""
        if user_info_res.status_code == 200:
            gmail_address = user_info_res.json().get("email", "")
            
        if gmail_address:
            gateway_db.set_config("gmail_address", gmail_address)
        if refresh_token:
            gateway_db.set_config("gmail_refresh_token", refresh_token)
        gateway_db.set_config("email_auth_method", "oauth2")
        gateway_db.set_config("email_gateway_active", "true")
        
        logger.info(f"Successfully connected Google OAuth2 for Gmail: {gmail_address}")
        return RedirectResponse(url=f"/admin?oauth_success=true&email={quote(gmail_address)}")
        
    except Exception as e:
        logger.error(f"Google OAuth callback processing error: {e}")
        return RedirectResponse(url=f"/admin?oauth_error={quote(str(e))}")
