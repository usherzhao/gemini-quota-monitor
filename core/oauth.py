"""
Gemini Quota Monitor - Google Official OAuth 2.0 Flow
Implements browser-based OAuth authorization, local callback receiver, token exchange, and token refresh.
Directly compatible with CLIProxyAPI / Antigravity Google OAuth.
"""

from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
import base64
import json
import os
import socket
import threading
import time
from typing import Any, Dict, Optional, Tuple
import urllib.parse
import webbrowser

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from utils.logger import logger

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    import requests as cffi_requests

# Google Cloud Code Desktop Public Client Credentials
_CID_PREFIX = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep"
_CID_SUFFIX = ".apps.googleusercontent.com"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", f"{_CID_PREFIX}{_CID_SUFFIX}")

_SEC_B64 = "R09DU1BYLUs1OEZXUjQ4NkxkTEoxbUxCOHNYQzR6NnFEQWY="
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", base64.b64decode(_SEC_B64.encode("ascii")).decode("utf-8"))
DEFAULT_CALLBACK_PORT = 51121

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo?alt=json"


def is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def find_free_port(start_port: int = DEFAULT_CALLBACK_PORT) -> int:
    if is_port_available(start_port):
        return start_port
    for p in range(start_port + 1, start_port + 20):
        if is_port_available(p):
            return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles HTTP redirect from Google OAuth."""
    auth_code: Optional[str] = None
    auth_error: Optional[str] = None

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Google 授权成功</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #0f172a; color: #f8fafc; }
                    .card { background: #1e293b; padding: 40px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; max-width: 420px; border: 1px solid #334155; }
                    .icon { font-size: 54px; margin-bottom: 16px; color: #10b981; }
                    h2 { margin: 0 0 12px; color: #f8fafc; }
                    p { color: #94a3b8; font-size: 14px; line-height: 1.6; margin: 0 0 24px; }
                    .hint { font-size: 12px; color: #64748b; }
                </style>
            </head>
            <body>
                <div class="card">
                    <div class="icon">✓</div>
                    <h2>Google 授权成功</h2>
                    <p>已成功关联您的 Google 账号！Gemini Quota Monitor 正在同步您的额度数据，您可以安全关闭此标签页。</p>
                    <div class="hint">Gemini Quota Monitor</div>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))
        else:
            err = params.get("error", ["未知错误"])[0]
            OAuthCallbackHandler.auth_error = err
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            err_html = f"<html><body style='background:#0f172a;color:#f8fafc;font-family:sans-serif;text-align:center;padding:50px;'><h2>授权失败: {err}</h2></body></html>"
            self.wfile.write(err_html.encode("utf-8"))

    def log_message(self, format, *args):
        # Suppress standard logging to avoid console pollution
        pass


class GoogleOAuthClient:
    """Client for performing Google OAuth 2.0 Authorization Code Flow."""

    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url

    def _build_session(self):
        if HAS_CURL_CFFI:
            session = cffi_requests.Session(impersonate="chrome124")
        else:
            session = cffi_requests.Session()

        if self.proxy_url:
            session.proxies = {
                "http": self.proxy_url,
                "https": self.proxy_url,
            }
        return session

    def build_auth_url(self, port: int, state: str = "gemini_state") -> Tuple[str, str]:
        """Builds Google OAuth authorization URL and redirect URI."""
        redirect_uri = f"http://localhost:{port}/oauth-callback"
        params = {
            "access_type": "offline",
            "client_id": GOOGLE_CLIENT_ID,
            "prompt": "consent",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "state": state,
        }
        query_str = urllib.parse.urlencode(params)
        return f"{GOOGLE_AUTH_ENDPOINT}?{query_str}", redirect_uri

    def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """Exchanges authorization code for access_token and refresh_token."""
        session = self._build_session()
        payload = {
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "antigravity/cli/1.0.13 (aidev_client; os_type=windows; arch=amd64)",
        }
        resp = session.post(GOOGLE_TOKEN_ENDPOINT, data=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Token 交换失败 ({resp.status_code}): {resp.text}")

        data = resp.json()
        return data

    def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refreshes an expired access_token using a refresh_token."""
        if not refresh_token:
            raise ValueError("缺少 refresh_token")

        session = self._build_session()
        payload = {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "antigravity/cli/1.0.13 (aidev_client; os_type=windows; arch=amd64)",
        }
        resp = session.post(GOOGLE_TOKEN_ENDPOINT, data=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Token 刷新失败 ({resp.status_code}): {resp.text}")

        return resp.json()

    def fetch_user_info(self, access_token: str) -> Dict[str, Any]:
        """Fetches user profile email from Google UserInfo endpoint."""
        session = self._build_session()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "antigravity/cli/1.0.13 (aidev_client; os_type=windows; arch=amd64)",
        }
        resp = session.get(GOOGLE_USERINFO_ENDPOINT, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return {}


class GoogleOAuthThread(QThread):
    """Background worker thread to manage local HTTP callback server and browser flow."""
    auth_started = pyqtSignal(str)  # auth_url
    auth_success = pyqtSignal(dict) # result payload
    auth_failed = pyqtSignal(str)   # error_msg

    def __init__(self, proxy_url: Optional[str] = None):
        super().__init__()
        self.proxy_url = proxy_url
        self.client = GoogleOAuthClient(proxy_url=proxy_url)
        self._is_cancelled = False
        self._server: Optional[HTTPServer] = None

    def cancel(self):
        self._is_cancelled = True
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass

    def run(self):
        port = find_free_port(DEFAULT_CALLBACK_PORT)
        auth_url, redirect_uri = self.client.build_auth_url(port)

        OAuthCallbackHandler.auth_code = None
        OAuthCallbackHandler.auth_error = None

        try:
            self._server = HTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
        except Exception as e:
            self.auth_failed.emit(f"无法启动本地授权服务 (端口 {port}): {e}")
            return

        self.auth_started.emit(auth_url)

        # Open browser in a non-blocking background thread
        threading.Thread(target=lambda: webbrowser.open(auth_url), daemon=True).start()

        # Handle one request with timeout loop
        start_time = time.time()
        timeout_seconds = 180  # 3 minutes

        while not self._is_cancelled and (time.time() - start_time) < timeout_seconds:
            self._server.timeout = 1.0
            self._server.handle_request()
            if OAuthCallbackHandler.auth_code or OAuthCallbackHandler.auth_error:
                break

        try:
            self._server.server_close()
        except Exception:
            pass

        if self._is_cancelled:
            self.auth_failed.emit("授权已取消")
            return

        if OAuthCallbackHandler.auth_error:
            self.auth_failed.emit(f"Google 授权返回错误: {OAuthCallbackHandler.auth_error}")
            return

        code = OAuthCallbackHandler.auth_code
        if not code:
            self.auth_failed.emit("等待 Google 授权超时（超过3分钟）")
            return

        # Exchange code for tokens
        try:
            token_data = self.client.exchange_code(code, redirect_uri)
            access_token = token_data.get("access_token", "")
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 3600)

            expiry_dt = datetime.now() + timedelta(seconds=expires_in)

            # Fetch user email
            user_info = self.client.fetch_user_info(access_token)
            email = user_info.get("email", "")

            result = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": expires_in,
                "token_expiry": expiry_dt.isoformat(),
                "account_email": email,
            }
            self.auth_success.emit(result)
        except Exception as e:
            logger.error(f"Token exchange error: {e}", exc_info=True)
            self.auth_failed.emit(f"Token 交换异常: {e}")
