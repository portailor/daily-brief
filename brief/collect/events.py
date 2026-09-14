"""다가오는 일정 — 금리 결정, 미국 주요 지표 발표, 대형주 실적, 선물옵션 만기.

일정은 날짜 하나만 틀려도 없느니만 못하다. 그래서 출처를 이렇게 정했다.

  미국 지표 발표일   FRED 발표 예정일 API        매번 받아온다 (공식, 자동)
  미국 기업 실적일   Finnhub 실적 캘린더          매번 받아온다
  FOMC              연준 공식 페이지 확인값       아래 고정값 (연 1회 갱신)
  한국 금통위        한국은행 공식 페이지 확인값    아래 고정값 (연 1회 갱신)
  선물옵션 동시만기   거래소 규칙으로 계산          휴장일과 겹치면 앞당겨지므로 '규칙상'

시각은 한국 투자자 기준으로 KST 로 바꿔 보여준다. 미국 발표는 대부분
한국시간 저녁이나 새벽이라, 날짜만 보면 하루가 어긋나기 쉽다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.clock import KST, label                 # noqa: E402
from brief.collect.macro import _load_env          # noqa: E402
from brief.retry import with_retry                 # noqa: E402

NY = ZoneInfo("America/New_York")

# ── 고정 일정 ────────────────────────────────────────────────
# FOMC: federalreserve.gov/monetarypolicy/fomccalendars.htm (2026-09-14 확인)
# 결정 발표는 회의 둘째 날 14:00(미 동부) = 한국시간 다음날 새벽.
FOMC_DECISION_DAYS = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
    "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
]
FOMC_WITH_PROJECTIONS = {"2026-03-18", "2026-06-17", "2026-09-16", "2026-12-09",
                         "2027-03-17", "2027-06-09", "2027-09-15", "2027-12-08"}

# 한국은행 통화정책방향 결정회의: bok.or.kr 결정회의 목록 (2026-09-14 확인)
# 2027년 일정은 보통 전년 10월 말에 발표된다 — 발표되면 추가할 것.
BOK_DECISION_DAYS = [
    "2026-01-15", "2026-02-26", "2026-04-10", "2026-05-28",
    "2026-07-16", "2026-08-27", "2026-10-22", "2026-11-26",
]

# FRED 발표(release) 번호 → 이름, 중요도. 미국 동부 08:30 발표.
# 제목에 '미국' 을 붙이지 않는다 — 화면에서 국가 표시가 따로 붙는다.
FRED_RELEASES = {
    10: ("소비자물가(CPI)", 3),
    50: ("고용보고서", 3),
    46: ("생산자물가(PPI)", 2),
    53: ("GDP", 2),
    54: ("PCE 물가", 3),
    9:  ("소매판매", 2),
}

# 실적 캘린더에는 소형주가 수십 개씩 섞여 온다. 시장을 움직이는 대형주만 남긴다.
MAJOR_US = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "NVDA": "엔비디아", "GOOGL": "알파벳",
    "AMZN": "아마존", "META": "메타", "TSLA": "테슬라", "AVGO": "브로드컴",
    "ORCL": "오라클", "ADBE": "어도비", "CRM": "세일즈포스", "AMD": "AMD",
    "INTC": "인텔", "MU": "마이크론", "NFLX": "넷플릭스", "COST": "코스트코",
    "WMT": "월마트", "JPM": "JP모건", "BAC": "뱅크오브아메리카", "GS": "골드만삭스",
    "NKE": "나이키", "FDX": "페덱스", "DIS": "디즈니", "V": "비자", "MA": "마스터카드",
    "UNH": "유나이티드헬스", "LLY": "일라이릴리", "TSM": "TSMC", "ASML": "ASML",
    "QCOM": "퀄컴", "TXN": "텍사스인스트루먼트", "PLTR": "팔란티어",
}


@dataclass
class Event:
    day: date                 # 한국시간 기준 날짜
    at: time | None           # 한국시간 기준 시각 (모르면 None)
    title: str
    region: str               # KR / US
    importance: int           # 3 핵심 · 2 주요 · 1 참고
    note: str = ""

    def when(self) -> str:
        t = f" {self.at:%H:%M}" if self.at else ""
        return f"{label(self.day)}{t}"


def _ny_to_kst(d: date, hh: int, mm: int) -> datetime:
    """미국 동부 시각을 한국시간으로. 서머타임은 zoneinfo 가 처리한다."""
    return datetime.combine(d, time(hh, mm), NY).astimezone(KST)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def fixed_events(start: date, end: date) -> list[Event]:
    out: list[Event] = []

    for s in FOMC_DECISION_DAYS:
        k = _ny_to_kst(date.fromisoformat(s), 14, 0)
        if start <= k.date() <= end:
            sep = " · 경제전망 발표" if s in FOMC_WITH_PROJECTIONS else ""
            out.append(Event(k.date(), k.time(), "FOMC 금리 결정", "US", 3,
                             f"미국 기준금리 발표{sep}"))

    for s in BOK_DECISION_DAYS:
        d = date.fromisoformat(s)
        if start <= d <= end:
            out.append(Event(d, time(10, 0), "한국은행 금통위 금리 결정", "KR", 3,
                             "오전 발표, 이후 총재 기자간담회"))

    # 분기 선물옵션 동시만기 — 규칙상 날짜. 휴장일과 겹치면 앞당겨진다.
    for y in {start.year, end.year}:
        for m in (3, 6, 9, 12):
            kr = _nth_weekday(y, m, 3, 2)          # 둘째 목요일
            if start <= kr <= end:
                out.append(Event(kr, None, "국내 선물옵션 동시만기", "KR", 2,
                                 "만기일 전후 변동성이 커질 수 있음 (규칙상 날짜)"))
            us = _nth_weekday(y, m, 4, 3)          # 셋째 금요일
            # 만기는 '그날 거래'가 의미라 한국시간으로 바꾸지 않는다.
            # 바꾸면 미국 금요일 장 마감이 한국 토요일로 찍혀 오해를 부른다.
            if start <= us <= end:
                out.append(Event(us, None, "트리플위칭(분기 만기)", "US", 1,
                                 "미국 날짜 기준 · 규칙상 날짜"))
    return out


def fred_events(start: date, end: date, api_key: str) -> list[Event]:
    out: list[Event] = []
    # 발표일은 미국 날짜 기준이고, 한국시간으로는 같은 날 밤이다.
    q_start, q_end = start - timedelta(days=1), end
    for rid, (title, imp) in FRED_RELEASES.items():
        res = with_retry(lambda rid=rid: requests.get(
            "https://api.stlouisfed.org/fred/release/dates",
            params={"release_id": rid, "api_key": api_key, "file_type": "json",
                    "realtime_start": q_start.isoformat(),
                    "realtime_end": q_end.isoformat(),
                    "include_release_dates_with_no_data": "true",
                    "sort_order": "asc", "limit": 20},
            timeout=20), attempts=2, label=f"FRED release {rid}")
        if not res.ok:
            continue
        for r in res.json().get("release_dates", []):
            k = _ny_to_kst(date.fromisoformat(r["date"]), 8, 30)
            if start <= k.date() <= end:
                out.append(Event(k.date(), k.time(), title, "US", imp, "미국 동부 08:30 발표"))
    return out


def earnings_events(start: date, end: date, api_key: str,
                    extra: dict[str, str] | None = None) -> list[Event]:
    names = {**MAJOR_US, **(extra or {})}
    res = with_retry(lambda: requests.get(
        "https://finnhub.io/api/v1/calendar/earnings",
        params={"from": (start - timedelta(days=1)).isoformat(),
                "to": end.isoformat(), "token": api_key},
        timeout=20), attempts=2, label="Finnhub 실적")
    if not res.ok:
        return []

    out: list[Event] = []
    for e in res.json().get("earningsCalendar", []):
        sym = e.get("symbol", "")
        if sym not in names:
            continue
        us_day = date.fromisoformat(e["date"])
        hour = e.get("hour", "")
        if hour == "amc":      # 미국 장 마감 후 → 한국시간 다음날 새벽
            k = _ny_to_kst(us_day, 16, 30)
            note = "미국 장 마감 후 발표"
        elif hour == "bmo":    # 미국 장 시작 전 → 한국시간 같은 날 밤
            k = _ny_to_kst(us_day, 7, 0)
            note = "미국 장 시작 전 발표"
        else:
            k = _ny_to_kst(us_day, 12, 0)
            note = "발표 시각 미정"
        if start <= k.date() <= end:
            out.append(Event(k.date(), None, f"{names[sym]} 실적", "US", 2, note))
    return out


def upcoming(start: date, end: date) -> tuple[list[Event], list[str]]:
    """start~end(한국시간) 사이 일정. 두 번째 값은 가져오지 못한 출처 목록."""
    env = _load_env()
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    watch_us = {t: t for t in (cfg.get("watchlist", {}) or {}).get("us", []) or []}

    events = fixed_events(start, end)
    missing: list[str] = []

    if env.get("FRED_API_KEY"):
        try:
            events += fred_events(start, end, env["FRED_API_KEY"])
        except Exception:                                   # noqa: BLE001
            missing.append("미국 지표 발표일")
    if env.get("FINNHUB_API_KEY"):
        try:
            events += earnings_events(start, end, env["FINNHUB_API_KEY"], watch_us)
        except Exception:                                   # noqa: BLE001
            missing.append("기업 실적일")

    events.sort(key=lambda e: (e.day, e.at or time(23, 59), -e.importance))
    return events, missing


if __name__ == "__main__":
    from brief.clock import today_kst
    t = today_kst()
    evs, miss = upcoming(t, t + timedelta(days=13))
    print(f"=== {label(t)} ~ {label(t + timedelta(days=13))} 일정 {len(evs)}건 ===")
    for e in evs:
        star = "★" * e.importance
        print(f"  {e.when():<16} [{e.region}] {e.title:<22} {star:<3} {e.note}")
    if miss:
        print("가져오지 못함:", ", ".join(miss))
