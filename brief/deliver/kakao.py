"""카카오톡 '나에게 보내기' 발송기.

access_token 은 6시간이면 만료되므로 매 발송마다 refresh_token 으로 갱신한다.
카카오가 refresh_token 을 새로 내려주면(잔여 1개월 미만일 때) 그것도 저장한다.

텍스트 템플릿 제약:
  text 최대 200자, 링크 1개, 버튼 1개.
  그래서 브리핑 전문은 못 담고, 핵심만 쪼개 보낸 뒤 마지막에 링크를 건다.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN_PATH = ROOT / "config" / ".kakao_token.json"

sys.path.insert(0, str(ROOT))
from brief.retry import with_retry  # noqa: E402

SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
TEXT_LIMIT = 200


class KakaoError(RuntimeError):
    pass


def _load() -> dict:
    if not TOKEN_PATH.exists():
        raise KakaoError(
            "토큰 파일이 없습니다. 먼저 최초 인증을 실행하세요:\n"
            "  python brief/deliver/kakao_auth.py")
    return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    TOKEN_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def refresh_access_token() -> str:
    """refresh_token 으로 access_token 재발급. 갱신된 refresh_token 도 반영."""
    cfg = _load()
    payload = {
        "grant_type": "refresh_token",
        "client_id": cfg["rest_api_key"],
        "refresh_token": cfg["refresh_token"],
    }
    if cfg.get("client_secret"):
        payload["client_secret"] = cfg["client_secret"]

    res = requests.post(TOKEN_URL, data=payload, timeout=15)

    if res.status_code != 200:
        raise KakaoError(
            f"토큰 갱신 실패 [{res.status_code}] {res.text}\n"
            "refresh_token 이 만료됐을 수 있습니다 (2개월). "
            "kakao_auth.py 를 다시 실행하세요.")

    tok = res.json()
    cfg["access_token"] = tok["access_token"]
    if "refresh_token" in tok:                 # 잔여 1개월 미만일 때만 내려옴
        cfg["refresh_token"] = tok["refresh_token"]
    _save(cfg)
    return cfg["access_token"]


def send_text(text: str,
              link_url: str | None = None,
              button_title: str = "전체 브리핑 보기",
              access_token: str | None = None) -> None:
    """텍스트 한 건 발송. 200자를 넘으면 잘라내지 않고 예외를 낸다."""
    if len(text) > TEXT_LIMIT:
        raise KakaoError(
            f"텍스트가 {len(text)}자로 카카오 제한({TEXT_LIMIT}자)을 넘습니다.\n"
            f"→ {text[:60]}...")

    token = access_token or refresh_access_token()
    template: dict = {"object_type": "text", "text": text}

    if link_url:
        template["link"] = {"web_url": link_url, "mobile_web_url": link_url}
        template["button_title"] = button_title
    else:
        template["link"] = {}

    res = with_retry(
        lambda: requests.post(
            SEND_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={"template_object": json.dumps(template, ensure_ascii=False)},
            timeout=20),
        label="카카오 발송")

    if res.status_code != 200:
        raise KakaoError(f"발송 실패 [{res.status_code}] {res.text}")
    if res.json().get("result_code") != 0:
        raise KakaoError(f"발송 실패: {res.text}")


def send_sequence(messages: list[str],
                  link_url: str | None = None,
                  delay: float = 0.6) -> int:
    """여러 메시지를 순서대로 발송. 마지막 건에만 링크 버튼을 붙인다."""
    token = refresh_access_token()
    for i, msg in enumerate(messages):
        last = i == len(messages) - 1
        send_text(msg,
                  link_url=link_url if last else None,
                  access_token=token)
        if not last:
            time.sleep(delay)
    return len(messages)


if __name__ == "__main__":
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        send_text(
            f"✅ 경제 브리핑 연결 테스트\n\n{now}\n\n"
            "이 메시지가 보이면 카카오 발송 경로가 정상입니다.\n"
            "이제 매일 오전 7시에 브리핑이 도착합니다.")
        print("✓ 발송 성공 — 카카오톡 '나와의 채팅'을 확인하세요.")
    except KakaoError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)
