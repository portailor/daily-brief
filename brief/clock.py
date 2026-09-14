"""날짜 판단을 한곳에 모은다.

이 시스템은 한국시간 아침에 돈다. 그 시점에 '오늘' 날짜가 붙은 시세는
전부 아직 끝나지 않은 거래다 — 원유·금 선물은 월요일 새벽에 이미 장중 값이
'오늘' 날짜로 들어오고, 낮에 수동 실행하면 코스피 장중 값이 들어온다.
이런 값을 저장하면 전일 대비·σ·채점이 전부 오염된다.

또 일부 공식 계열(한국 기준금리, 미 실효 기준금리)은 주말에도 날짜가 찍혀서,
'가장 최근 날짜'를 기준으로 삼으면 토요일에 거래가 있었던 것처럼 보인다.

그래서 저장 단계에서부터 '마감된 평일 거래일'만 받는다.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
WEEKDAY_KR = "월화수목금토일"


def today_kst() -> date:
    return datetime.now(KST).date()


def is_finished_session(d: date | str, today: date | None = None) -> bool:
    """마감된 평일 거래일인가.

    오늘(KST) 이후 날짜는 아직 진행 중이거나 미래이므로 제외한다.
    한국시간 아침 기준으로 미국장의 '어제' 거래는 이미 끝났으므로 포함된다.
    """
    if isinstance(d, str):
        d = date.fromisoformat(d)
    today = today or today_kst()
    return d < today and d.weekday() < 5


def label(d: date | str) -> str:
    """'9/11(금)' 형식."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return f"{d.month}/{d.day}({WEEKDAY_KR[d.weekday()]})"


def label_long(d: date | str) -> str:
    """'2026년 9월 11일 (금)' 형식."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return f"{d.year}년 {d.month}월 {d.day}일 ({WEEKDAY_KR[d.weekday()]})"
