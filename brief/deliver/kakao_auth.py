"""카카오 '나에게 보내기' 최초 인증 — 딱 한 번만 실행하면 된다.

실행하면 브라우저가 열리고, 카카오 로그인 + 동의를 마치면
refresh_token 을 config/.kakao_token.json 에 저장한다.
이후 매일 발송은 이 파일을 읽어 자동으로 access_token 을 갱신한다.

  access_token   6시간   → 매번 갱신해서 씀
  refresh_token  2개월   → 갱신될 때마다 유효기간도 연장됨
                          (2개월 이상 브리핑을 안 돌리면 재인증 필요)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN_PATH = ROOT / "config" / ".kakao_token.json"
REDIRECT_URI = "http://localhost:5000/oauth"
PORT = 5000
# talk_message = 메시지 전송, friends = 카카오 서비스 내 친구목록.
# friends 가 빠지면 친구 목록 조회(/v1/api/talk/friends)가 403 으로 막힌다.
SCOPE = "talk_message,friends"

AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"

_code: str | None = None


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        global _code
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if "code" in params:
            _code = params["code"][0]
            body = "<h2>인증 완료</h2><p>이 창을 닫고 터미널로 돌아가세요.</p>"
        else:
            err = params.get("error_description", ["알 수 없는 오류"])[0]
            body = f"<h2>인증 실패</h2><p>{err}</p>"
        self.wfile.write(f"<html><meta charset='utf-8'><body style='font-family:sans-serif;"
                         f"padding:40px'>{body}</body></html>".encode())

    def log_message(self, *args):                        # 서버 로그 침묵
        pass


def authorize(rest_api_key: str, client_secret: str = "") -> dict:
    params = {
        "client_id": rest_api_key,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    server = HTTPServer(("localhost", PORT), _Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print("브라우저에서 카카오 로그인 + 동의를 진행하세요.")
    print("창이 안 열리면 아래 주소를 직접 복사해 여세요:\n")
    print(url, "\n")
    webbrowser.open(url)

    for _ in range(180):                                 # 최대 3분 대기
        if _code:
            break
        threading.Event().wait(1)
    server.server_close()

    if not _code:
        raise TimeoutError("인가 코드를 받지 못했습니다. Redirect URI 등록을 확인하세요.")

    payload = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": REDIRECT_URI,
        "code": _code,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    res = requests.post(TOKEN_URL, data=payload, timeout=15)

    if res.status_code != 200:
        hint = ""
        if "KOE010" in res.text:
            hint = ("\n\n→ Client Secret 문제입니다. 카카오 콘솔에서\n"
                    "   [카카오 로그인] › [보안] › Client Secret 을 확인하세요.\n"
                    "   '사용함' 상태라면 그 코드값을 두 번째 인자로 넘겨야 합니다:\n"
                    "     python brief/deliver/kakao_auth.py <REST_API_KEY> <CLIENT_SECRET>")
        raise RuntimeError(f"토큰 발급 실패 [{res.status_code}] {res.text}{hint}")

    token = res.json()
    if "friends" not in token.get("scope", ""):
        print("
주의: 'friends' 동의항목이 빠졌습니다. 친구에게는 보낼 수 없습니다.")
        print("      카카오 개발자 콘솔 > 카카오 로그인 > 동의항목에서")
        print("      '카카오 서비스 내 친구목록'을 켠 뒤 다시 실행하세요.
")
    if "talk_message" not in token.get("scope", ""):
        print("\n⚠ 경고: talk_message 스코프가 없습니다.")
        print("  카카오 로그인 > 동의항목 에서 '카카오톡 메시지 전송'을 활성화하세요.")
    return token


def save(token: dict, rest_api_key: str, client_secret: str = "") -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps({
        "rest_api_key": rest_api_key,
        "client_secret": client_secret,
        "access_token": token["access_token"],
        "refresh_token": token["refresh_token"],
        "scope": token.get("scope", ""),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        TOKEN_PATH.chmod(0o600)


if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else os.getenv("KAKAO_REST_API_KEY", "")
    key = key.strip() or input("카카오 REST API 키를 붙여넣으세요: ").strip()

    secret = sys.argv[2] if len(sys.argv) > 2 else os.getenv("KAKAO_CLIENT_SECRET", "")
    secret = secret.strip()

    tok = authorize(key, secret)
    save(tok, key, secret)
    print(f"\n✓ 저장 완료: {TOKEN_PATH}")
    print(f"  scope: {tok.get('scope')}")
    print("\n이제 다음 명령으로 테스트 발송을 해보세요:")
    print("  python brief/deliver/kakao.py")
