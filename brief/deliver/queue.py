"""대기열 영상 — 조건이 맞는 날, 브리핑 쇼츠보다 먼저 올린다.

queue/queue.json 에 영상을 적어 두면, 쇼츠 올리기 단계에서 오늘 조건에 맞는 첫 영상을
브리핑 쇼츠 바로 앞에 올린다. 올린 id 는 data/state.json 의 queue_done 에 남아
(발송 기록과 함께 커밋된다) 다시 올라가지 않는다.

항목 예:
  {"id": "ep01", "file": "queue/ep01.mp4", "title": "...", "description": "...",
   "tags": ["하찮이"], "category": "23",
   "from": "2026-09-28", "until": "2026-10-02",   # 브리핑 날짜 기준, 양 끝 포함
   "mood": "down",                                # 선택 — 브리핑 자켓 색 (down=파랑, up=빨강)
   "hold": true}                                  # 선택 — true 면 자동으로 올리지 않고 대기만 (올릴 때 지운다)

기간 안에 조건이 한 번도 맞지 않으면 올리지 않고 넘어간다(로그만 남는다).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
QUEUE = ROOT / "queue" / "queue.json"


def load(path: Path = QUEUE) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def due(items: list[dict], brief_date: str, mood: str, done: list[str]) -> dict | None:
    """오늘(브리핑 날짜) 올릴 첫 항목. ISO 날짜 문자열은 사전순 비교가 곧 날짜 비교다."""
    for it in items:
        if it["id"] in done or it.get("hold"):
            continue
        if not it.get("from", "") <= brief_date <= it.get("until", "9999-12-31"):
            continue
        if it.get("mood") and it["mood"] != mood:
            continue
        return it
    return None
