"""유튜브 쇼츠 올리기 — YouTube Data API v3, 카톡 발송 직후.

필요한 값 (config/.env 또는 GitHub Secrets)
  YT_CLIENT_ID, YT_CLIENT_SECRET   Google Cloud 의 OAuth 클라이언트(데스크톱 앱)
  YT_REFRESH_TOKEN                 scripts/youtube_auth.py 로 한 번 받는다

알아 둘 것 (developers.google.com, 2026-09 확인)
  - 감사(audit)를 받지 않은 API 프로젝트로 올린 영상은 공개 설정과 상관없이 '비공개'로
    잠긴다. 공개하려면 스튜디오에서 직접 바꾸거나 YouTube API 감사를 신청해야 한다.
  - 하루 할당량 10,000 단위, 업로드 한 번에 약 100 단위 — 하루 한 편은 넉넉하다.
  - 60초 이하·세로 영상은 자동으로 쇼츠로 분류된다.

토큰 값은 절대 출력하지 않는다 (공개 저장소 로그).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
KEYS = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")


def _env() -> dict[str, str]:
    import os
    from brief.collect.macro import _load_env
    env = _load_env()
    return {k: (os.environ.get(k) or env.get(k, "")).strip() for k in KEYS}


def configured() -> bool:
    return all(_env().values())


def _access_token(env: dict[str, str]) -> str:
    r = requests.post(TOKEN_URL, data={
        "client_id": env["YT_CLIENT_ID"], "client_secret": env["YT_CLIENT_SECRET"],
        "refresh_token": env["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        # 본문에 토큰이 들어 있지 않은 오류 코드만 남긴다
        raise RuntimeError(f"유튜브 토큰 갱신 실패 ({r.status_code} {r.json().get('error', '')})")
    return r.json()["access_token"]


def upload(path: str | Path, title: str, description: str, privacy: str = "public",
           tags: list[str] | None = None, category: str = "25") -> tuple[str, str]:
    """영상을 올리고 (영상 주소, 실제 공개 상태)를 돌려준다."""
    path = Path(path)
    env = _env()
    token = _access_token(env)
    meta = {
        "snippet": {"title": title[:100], "description": description[:4900],
                    "tags": tags or ["경제", "주식", "코스피", "브리핑"],
                    "categoryId": category,          # 25 뉴스/정치, 23 코미디
                    "defaultLanguage": "ko", "defaultAudioLanguage": "ko"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    size = path.stat().st_size
    start = requests.post(UPLOAD_URL, timeout=30, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
        data=json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    if start.status_code != 200:
        raise RuntimeError(f"유튜브 업로드 시작 실패 ({start.status_code}): {start.text[:300]}")
    with path.open("rb") as f:
        put = requests.put(start.headers["Location"], data=f, timeout=600, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
            "Content-Length": str(size)})
    if put.status_code not in (200, 201):
        raise RuntimeError(f"유튜브 업로드 실패 ({put.status_code}): {put.text[:300]}")
    body = put.json()
    status = (body.get("status") or {}).get("privacyStatus", "")
    return f"https://youtube.com/shorts/{body['id']}", status
