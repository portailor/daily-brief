"""유튜브 업로드 권한을 한 번 받는다.

  python scripts/youtube_auth.py

1) config/.env 에 YT_CLIENT_ID, YT_CLIENT_SECRET 이 없으면 물어보고 저장한다.
2) 브라우저에서 구글 로그인 → 채널 선택 → 허용.
3) 받은 refresh token 을 config/.env 에 저장하고, GitHub Secrets 3개도 등록한다(gh CLI).
토큰 값은 화면에 찍지 않는다.
"""
from __future__ import annotations

import secrets
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "config" / ".env"
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"

_got: dict[str, str] = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _got.update({k: v[0] for k, v in q.items()})
        ok = "code" in q
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "인증 완료 — 이 창을 닫고 터미널로 돌아가세요." if ok else f"인증 실패: {q.get('error', ['?'])[0]}"
        self.wfile.write(f"<html><body style='font-family:sans-serif;padding:40px'><h2>{msg}</h2>"
                         "</body></html>".encode("utf-8"))

    def log_message(self, *args):
        pass


def _read_env() -> dict[str, str]:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _set_env(key: str, value: str) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [l for l in lines if not l.strip().startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    env = _read_env()
    for key, label in (("YT_CLIENT_ID", "클라이언트 ID"), ("YT_CLIENT_SECRET", "클라이언트 보안 비밀번호")):
        if not env.get(key):
            env[key] = input(f"{label} 붙여넣기: ").strip()
            _set_env(key, env[key])

    state = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": env["YT_CLIENT_ID"], "redirect_uri": REDIRECT, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state})
    server = HTTPServer(("127.0.0.1", PORT), _Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    print("브라우저에서 구글 로그인 → 브리핑을 올릴 채널 선택 → 허용을 눌러 주세요.")
    print("'확인되지 않은 앱' 경고가 나오면: 고급 → (안전하지 않음)으로 이동. 본인이 만든 앱입니다.")
    webbrowser.open(url)
    for _ in range(300):
        if _got:
            break
        threading.Event().wait(1)
    server.server_close()

    if _got.get("state") != state or "code" not in _got:
        print(f"✗ 인증 실패: {_got.get('error', '시간 초과')}")
        return 1
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "code": _got["code"], "client_id": env["YT_CLIENT_ID"],
        "client_secret": env["YT_CLIENT_SECRET"], "redirect_uri": REDIRECT,
        "grant_type": "authorization_code"})
    if r.status_code != 200 or "refresh_token" not in r.json():
        print(f"✗ 토큰 교환 실패 ({r.status_code} {r.json().get('error', '')})")
        return 1
    _set_env("YT_REFRESH_TOKEN", r.json()["refresh_token"])
    print("✓ config/.env 에 저장했습니다")

    env = _read_env()
    for key in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        p = subprocess.run(["gh", "secret", "set", key], input=env[key], text=True,
                           capture_output=True, cwd=ROOT)
        print(f"{'✓' if p.returncode == 0 else '✗'} GitHub Secret {key}"
              + ("" if p.returncode == 0 else f" 등록 실패: {p.stderr.strip()[:200]}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
