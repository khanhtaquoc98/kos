import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

import gateway_db
from handlers import (
    pages,
    payment,
    admin,
    oauth,
    webhooks
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mbbank-webhook")

app = FastAPI(title="KOS Admin Gateway", version="0.3.0")

@app.middleware("http")
async def check_db_initialization(request: Request, call_next):
    if gateway_db.db_initialization_error:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": gateway_db.db_initialization_error}
            )
        else:
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Lỗi Khởi Tạo Database - KOS</title>
                <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700&display=swap" rel="stylesheet">
                <style>
                    body {{ background: #0b0f19; color: #f3f4f6; font-family: 'Plus Jakarta Sans', sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }}
                    .card {{ background: rgba(17, 24, 39, 0.7); border: 1px solid rgba(239, 68, 68, 0.2); padding: 40px; border-radius: 20px; max-width: 500px; width: 100%; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5); backdrop-filter: blur(10px); }}
                    h2 {{ color: #ef4444; margin-top: 0; }}
                    p {{ color: #9ca3af; font-size: 0.95rem; line-height: 1.6; margin-bottom: 24px; text-align: left; }}
                    pre {{ background: rgba(0,0,0,0.3); padding: 16px; border-radius: 10px; text-align: left; overflow-x: auto; font-family: monospace; font-size: 0.85rem; border: 1px solid rgba(255,255,255,0.05); color: #e5e7eb; }}
                </style>
            </head>
            <body>
                <div class="card">
                    <div style="font-size: 3rem; margin-bottom: 20px;">⚠️</div>
                    <h2>Lỗi Kết Nối / Cấu Hình Supabase</h2>
                    <p>Ứng dụng không thể kết nối hoặc khởi tạo bảng dữ liệu trên Supabase:</p>
                    <pre>{gateway_db.db_initialization_error}</pre>
                    <p style="margin-top: 20px;"><b>Hướng dẫn khắc phục:</b><br>
                    1. Vào Vercel Settings -> Environment Variables, điền đúng <code>SUPABASE_URL</code> và <code>SUPABASE_KEY</code>.<br>
                    2. Kiểm tra xem bạn đã copy nội dung file <code>schema.sql</code> và bấm <b>Run</b> trong <b>Supabase SQL Editor</b> hay chưa.</p>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=500)
    return await call_next(request)

# Initialize Database on startup
@app.on_event("startup")
def startup_db():
    try:
        gateway_db.init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_route():
    fav_path = os.path.join(static_dir, "favicon.ico")
    if os.path.exists(fav_path):
        return FileResponse(fav_path)
    return HTMLResponse("", status_code=204)

# Include Modular APIRouters
app.include_router(pages.router)
app.include_router(payment.router)
app.include_router(admin.router)
app.include_router(oauth.router)
app.include_router(webhooks.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
