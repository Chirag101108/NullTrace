#!/usr/bin/env python3
"""Run the Null Trace / Grid System login locally using only Python's standard library."""

from __future__ import annotations

import hashlib
import hmac
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"  # Bind to this computer only.
TEAM_LOGO = "Null Trace Green Ribbon Logo.png"
PRODUCT_LOGO = "MicroGridOptimizer Energy Network Logo.png"


def load_dotenv() -> None:
    """Read the local .env credentials without requiring an external package."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        raise SystemExit("Missing .env. Copy .env.example to .env and set the admin password.")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip("\"'")


load_dotenv()
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
if len(ADMIN_PASSWORD) < 12:
    raise SystemExit("ADMIN_PASSWORD in .env must be at least 12 characters.")
PASSWORD_SALT = os.urandom(16)
PASSWORD_HASH = hashlib.pbkdf2_hmac("sha256", ADMIN_PASSWORD.encode(), PASSWORD_SALT, 310_000)
del ADMIN_PASSWORD


def password_matches(candidate: str) -> bool:
    candidate_hash = hashlib.pbkdf2_hmac("sha256", candidate.encode(), PASSWORD_SALT, 310_000)
    return hmac.compare_digest(candidate_hash, PASSWORD_HASH)


class LoginHandler(BaseHTTPRequestHandler):
    server_version = "GridSystemLocal/1.0"

    def send_page(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/login"):
            page = (ROOT / "Loginpage.html").read_text(encoding="utf-8")
            self.send_page(200, page.replace("{{ERROR}}", "").encode())
            return
        image_paths = {
            "/team-logo.png": TEAM_LOGO,
            "/product-logo.png": PRODUCT_LOGO,
        }
        if path in image_paths:
            try:
                self.send_page(200, (ROOT / image_paths[path]).read_bytes(), "image/png")
            except OSError:
                self.send_error(404)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if unquote(urlparse(self.path).path) != "/login":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 8192:
                self.send_error(413)
                return
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            username = fields.get("username", [""])[0]
            password = fields.get("password", [""])[0]
        except (ValueError, UnicodeDecodeError):
            username, password = "", ""

        username_ok = hmac.compare_digest(username.encode(), ADMIN_USERNAME.encode())
        if username_ok and password_matches(password):
            self.send_response(303)
            self.send_header("Location", "https://google.com")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        page = (ROOT / "Loginpage.html").read_text(encoding="utf-8")
        error = '<p class="error" role="alert">Wrong credentials. Check your username and password, then try again.</p>'
        self.send_page(401, page.replace("{{ERROR}}", error).encode())

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


if __name__ == "__main__":
    # Let the operating system choose an available local port. No host/port is kept in .env.
    httpd = HTTPServer((HOST, 0), LoginHandler)
    port = httpd.server_address[1]
    print(f"Grid System login: http://localhost:{port}")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nGrid System stopped.")
    finally:
        httpd.server_close()
