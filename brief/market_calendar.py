"""한국거래소·뉴욕증권거래소 휴장일 — 쇼츠가 '어느 장이 왜 쉬었는지/쉬는지' 말할 때 쓴다.

출처: exchange_calendars (XKRX·XNYS). 2026~2027 휴장일을 9/27 에 확인했다.
  - 공휴일·대체공휴일·연말 휴장(12/31)·근로자의 날(5/1)이 들어 있다.
  - 선거일 같은 임시 휴장은 라이브러리 갱신에 달려 있다 (2026-06-03 지방선거는 빠져 있었다).
    그래서 '쉬었다'는 판단은 이 달력만 믿지 않는다 — 실제 데이터의 기준일이 안 바뀌었는지와
    함께 본다 (brief/shorts/script.py). 이 달력은 이유(휴일 이름)와 미리 알리기에만 쓴다.

이름은 라이브러리의 영어 이름을 한국어로 옮긴다. 모르는 이름이면 '휴장일'로만 말한다.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

KR_NAMES = [   # (영어 이름 앞부분, 한국어)
    ("New Year's Day", "신정"), ("Seollal", "설 연휴"), ("Independence Movement Day", "삼일절"),
    ("Labor Day", "근로자의 날"), ("Children's Day", "어린이날"), ("Buddha's Birthday", "부처님오신날"),
    ("Memorial Day", "현충일"), ("National Liberation Day", "광복절"), ("Chuseok", "추석 연휴"),
    ("Korean National Foundation Day", "개천절"), ("Hangul Proclamation Day", "한글날"),
    ("Christmas", "성탄절"), ("End of Year Holiday", "연말 휴장일"),
]
US_NAMES = [
    ("New Year's Day", "새해 첫날"), ("Dr. Martin Luther King", "마틴 루서 킹 데이"),
    ("President", "대통령의 날"), ("Good Friday", "성금요일"), ("Memorial Day", "메모리얼 데이"),
    ("Juneteenth", "노예해방기념일(준틴스)"), ("July 4th", "독립기념일"), ("Independence Day", "독립기념일"),
    ("Labor Day", "노동절"), ("Thanksgiving", "추수감사절"), ("Christmas", "크리스마스"),
]
MARKET = {"KR": ("XKRX", KR_NAMES, "한국 증시"), "US": ("XNYS", US_NAMES, "미국 증시")}


@lru_cache(maxsize=4)
def _cal(code: str):
    import exchange_calendars as xc
    today = date.today()
    return xc.get_calendar(code, start=f"{today.year - 1}-01-01", end=f"{today.year + 1}-12-30")


@lru_cache(maxsize=4)
def _names(code: str) -> dict[date, str]:
    cal = _cal(code)
    out = {}
    lo, hi = cal.first_session, cal.last_session
    for ts, name in cal.regular_holidays.holidays(lo, hi, return_name=True).items():
        out[ts.date()] = name
    for ts in cal.adhoc_holidays:
        if lo <= ts <= hi:
            out.setdefault(ts.date(), "")
    return out


def is_open(market: str, day: date) -> bool:
    """그날 정규장이 열리는가. 주말은 닫힘."""
    if day.weekday() >= 5:
        return False
    cal = _cal(MARKET[market][0])
    try:
        return bool(cal.is_session(day.isoformat()))
    except Exception:                                   # noqa: BLE001  달력 범위 밖
        return True


def holiday_name(market: str, day: date) -> str | None:
    """평일 휴장일이면 한국어 이름('추석 연휴'), 이름을 모르면 '휴장일'. 여는 날이면 None."""
    if day.weekday() >= 5 or is_open(market, day):
        return None
    code, table, _ = MARKET[market]
    raw = _names(code).get(day, "")
    for prefix, ko in table:
        if raw.startswith(prefix):
            return ko
    return "휴장일"


def closed_run(market: str, start: date) -> list[date]:
    """start 부터 이어지는 평일 휴장일들 (주말은 건너뛰며 잇는다)."""
    out, d = [], start
    while True:
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        if holiday_name(market, d) is None:
            return out
        out.append(d)
        d += timedelta(days=1)


def next_weekday(day: date) -> date:
    d = day + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def prev_weekday(day: date) -> date:
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def label(market: str) -> str:
    return MARKET[market][2]
