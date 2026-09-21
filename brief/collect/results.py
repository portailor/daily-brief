"""지난 일정의 결과 — '이번 주 일정'에 올렸던 것이 실제로 어떻게 나왔나.

동화님 요청: "코스트코 실적이 25일이면, 다음날 브리핑에 결과를 띄워 달라."

원칙은 다른 곳과 같다. 공식·집계 수치를 옮길 뿐 해석하지 않는다.
'서프라이즈', '쇼크' 같은 말은 쓰지 않고 예상치와의 차이만 숫자로 적는다.

  실적        Finnhub 실적 캘린더 — 발표 뒤 epsActual/revenueActual 이 채워진다.
              예상치는 Finnhub 가 집계한 애널리스트 평균이다.
  미국 지표   FRED 원 계열 — 최신 관측치의 realtime_start 가 곧 발표일이다.
              그래서 발표 달력을 따로 맞추지 않아도 '이번에 새로 나온 값'인지 안다.
  금리 결정   FOMC: FRED 목표범위(DFEDTARL/U) · 한국은행: ECOS 기준금리(DB)

같은 결과를 매일 되풀이하지 않도록, 한 번 실은 결과는 data/state.json 의
results_shown 에 날짜와 함께 남긴다. 같은 날 다시 만들면 다시 싣는다.

Finnhub 실적 수치는 발표 뒤 며칠 늦게 채워지기도 한다(FedEx 9/16 발표분이
5일째 비어 있었다). 그래서 '다음날'로 못 박지 않고, 수치가 처음 들어온 날 싣는다.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief import db                                            # noqa: E402
from brief.collect.events import (BOK_DECISION_DAYS, FOMC_DECISION_DAYS,  # noqa: E402
                                  MAJOR_US, _ny_to_kst)
from brief.collect.macro import _load_env                       # noqa: E402
from brief.retry import with_retry                              # noqa: E402

FRED_OBS = "https://api.stlouisfed.org/fred/series/observations"
FINNHUB_CAL = "https://finnhub.io/api/v1/calendar/earnings"
STATE = ROOT / "data" / "state.json"

LOOKBACK_DAYS = 7          # 이만큼 지난 발표까지 살핀다 (집계가 늦는 경우 대비)
PENDING_DAYS = 3           # 발표는 지났는데 수치가 없을 때 '집계 전'으로 알리는 기간
KEEP_SHOWN_DAYS = 45       # results_shown 에 남겨 둘 기간


@dataclass
class Result:
    key: str                     # 중복 방지용 고유 키
    kind: str                    # earnings / macro / rate
    region: str                  # US / KR
    title: str                   # "코스트코 실적"
    period: str                  # "회계연도 2026년 4분기", "8월"
    lines: list[str] = field(default_factory=list)   # 결과 수치 (문장 아님, 값 나열)
    announced: str = ""          # 발표일(한국시간) "9/25(금)"
    pending: bool = False        # 발표는 됐지만 아직 수치가 없음
    source: str = ""


# ── 공통 ────────────────────────────────────────────────────

def _pct(new: float, old: float) -> float:
    return (new / old - 1) * 100


def _sign(v: float, digits: int = 1) -> str:
    return f"{v:+.{digits}f}"


def _usd(v: float) -> str:
    """96,745,402,720 → '967.5억 달러'. 1조 달러 이상은 조 단위."""
    eok = v / 1e8
    if eok >= 10_000:
        return f"{eok / 10_000:,.2f}조 달러"
    return f"{eok:,.1f}억 달러"


def _label(d: date) -> str:
    from brief.clock import label
    return label(d)


# ── 실적 (Finnhub) ─────────────────────────────────────────

def earnings(today: date, api_key: str, names: dict[str, str]) -> list[Result]:
    res = with_retry(lambda: requests.get(
        FINNHUB_CAL,
        params={"from": (today - timedelta(days=LOOKBACK_DAYS + 1)).isoformat(),
                "to": today.isoformat(), "token": api_key},
        timeout=25), attempts=2, label="Finnhub 실적 결과")
    if not res.ok:
        raise RuntimeError(f"Finnhub {res.status_code}")

    out: list[Result] = []
    for e in res.json().get("earningsCalendar", []):
        sym = e.get("symbol", "")
        if sym not in names:
            continue
        us_day = date.fromisoformat(e["date"])
        hh, mm = {"amc": (16, 30), "bmo": (7, 0)}.get(e.get("hour", ""), (12, 0))
        kst_day = _ny_to_kst(us_day, hh, mm).date()
        if kst_day > today or (today - kst_day).days > LOOKBACK_DAYS:
            continue

        q, y = e.get("quarter"), e.get("year")
        period = f"회계연도 {y}년 {q}분기" if q and y else ""
        key = f"earn:{sym}:{y}Q{q}"
        r = Result(key, "earnings", "US", f"{names[sym]} 실적", period,
                   announced=_label(kst_day),
                   source="Finnhub 집계 · 예상치는 애널리스트 평균")

        eps, eps_est = e.get("epsActual"), e.get("epsEstimate")
        rev, rev_est = e.get("revenueActual"), e.get("revenueEstimate")

        if eps is None and rev is None:
            if (today - kst_day).days <= PENDING_DAYS:
                r.pending = True
                out.append(r)
            continue

        if rev is not None:
            line = f"매출 {_usd(rev)}"
            if rev_est:
                line += f" · 예상 {_usd(rev_est)} 대비 {_sign(_pct(rev, rev_est))}%"
            r.lines.append(line)
        if eps is not None:
            line = f"주당순이익(EPS) {eps:,.2f}달러"
            if eps_est:
                line += f" · 예상 {eps_est:,.2f}달러 대비 {_sign(_pct(eps, eps_est))}%"
            r.lines.append(line)
        out.append(r)
    return out


# ── 미국 경제지표 (FRED) ───────────────────────────────────

def _fred(sid: str, api_key: str, n: int = 14) -> list[dict]:
    """최신순 관측치. 각 항목의 realtime_start = 그 값이 처음 공표된 날."""
    time.sleep(0.6)                       # FRED 는 짧은 시간에 몰리면 거절한다
    res = with_retry(lambda: requests.get(FRED_OBS, params={
        "series_id": sid, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": n}, timeout=25),
        attempts=2, label=f"FRED {sid}")
    res.raise_for_status()
    return [o for o in res.json().get("observations", []) if o["value"] not in (".", "")]


def _month(obs_date: str) -> str:
    return f"{int(obs_date[5:7])}월"


def _quarter(obs_date: str) -> str:
    return f"{(int(obs_date[5:7]) - 1) // 3 + 1}분기"


def _fresh(o: dict, today: date) -> date | None:
    """이번에 새로 공표된 값이면 공표일(미국 날짜)을 돌려준다."""
    pub = date.fromisoformat(o["realtime_start"])
    return pub if 0 <= (today - pub).days <= LOOKBACK_DAYS else None


def macro(today: date, api_key: str) -> list[Result]:
    out: list[Result] = []

    def add(key_sid: str, title: str, period: str, pub: date, lines: list[str], obs: dict):
        k = _ny_to_kst(pub, 8, 30).date()
        out.append(Result(f"macro:{key_sid}:{obs['date']}:{obs['realtime_start']}",
                          "macro", "US", title, period, lines, _label(k),
                          source=f"FRED {key_sid}"))

    # 소비자물가 — 미 노동통계국(BLS) 발표 관례를 따른다:
    #   '1년 전 대비'는 계절조정 전 지수(NSA), '전월 대비'는 계절조정 지수(SA).
    #   계절조정 지수로 1년 전 대비를 내면 보도된 수치와 0.1%p 가량 어긋날 수 있다.
    cpi = _fred("CPIAUCSL", api_key, 2)                     # SA — 전월 대비
    if len(cpi) >= 2 and (pub := _fresh(cpi[0], today)):
        mom = _pct(float(cpi[0]["value"]), float(cpi[1]["value"]))
        nsa = _fred("CPIAUCNS", api_key)                    # NSA — 1년 전 대비
        lines = []
        if len(nsa) >= 13 and nsa[0]["date"] == cpi[0]["date"]:
            yoy = _pct(float(nsa[0]["value"]), float(nsa[12]["value"]))
            lines.append(f"1년 전보다 {_sign(yoy)}% · 전월보다 {_sign(mom)}%")
        else:
            lines.append(f"전월보다 {_sign(mom)}%")
        core = _fred("CPILFENS", api_key)
        if len(core) >= 13 and core[0]["date"] == cpi[0]["date"]:
            lines.append(f"근원(식품·에너지 제외) 1년 전보다 "
                         f"{_sign(_pct(float(core[0]['value']), float(core[12]['value'])))}%")
        add("CPIAUCSL", "소비자물가(CPI)", _month(cpi[0]["date"]), pub, lines, cpi[0])

    # 고용 — 비농업 일자리 증감, 실업률
    pay = _fred("PAYEMS", api_key, 3)
    if len(pay) >= 2 and (pub := _fresh(pay[0], today)):
        diff = float(pay[0]["value"]) - float(pay[1]["value"])     # 천 명
        lines = [f"비농업 일자리 전월보다 {diff * 1000:+,.0f}명"]
        ur = _fred("UNRATE", api_key, 2)
        if ur and ur[0]["date"] == pay[0]["date"]:
            lines.append(f"실업률 {float(ur[0]['value']):.1f}%")
        add("PAYEMS", "고용보고서", _month(pay[0]["date"]), pub, lines, pay[0])

    # 생산자물가(최종수요) — CPI 와 같은 관례: 1년 전 대비 NSA, 전월 대비 SA
    ppi = _fred("PPIFIS", api_key, 2)
    if len(ppi) >= 2 and (pub := _fresh(ppi[0], today)):
        mom = _pct(float(ppi[0]["value"]), float(ppi[1]["value"]))
        nsa = _fred("PPIFID", api_key)
        if len(nsa) >= 13 and nsa[0]["date"] == ppi[0]["date"]:
            line = (f"1년 전보다 {_sign(_pct(float(nsa[0]['value']), float(nsa[12]['value'])))}%"
                    f" · 전월보다 {_sign(mom)}%")
        else:
            line = f"전월보다 {_sign(mom)}%"
        add("PPIFIS", "생산자물가(PPI)", _month(ppi[0]["date"]), pub, [line], ppi[0])

    # PCE 물가
    pce = _fred("PCEPI", api_key)
    if len(pce) >= 13 and (pub := _fresh(pce[0], today)):
        lines = [f"1년 전보다 {_sign(_pct(float(pce[0]['value']), float(pce[12]['value'])))}%"]
        core = _fred("PCEPILFE", api_key)
        if len(core) >= 13 and core[0]["date"] == pce[0]["date"]:
            lines.append(f"근원(식품·에너지 제외) 1년 전보다 "
                         f"{_sign(_pct(float(core[0]['value']), float(core[12]['value'])))}%")
        add("PCEPI", "PCE 물가", _month(pce[0]["date"]), pub, lines, pce[0])

    # 소매판매
    rs = _fred("RSAFS", api_key, 2)
    if len(rs) >= 2 and (pub := _fresh(rs[0], today)):
        add("RSAFS", "소매판매", _month(rs[0]["date"]), pub,
            [f"전월보다 {_sign(_pct(float(rs[0]['value']), float(rs[1]['value'])))}%"], rs[0])

    # GDP — 계열 자체가 '전분기 대비 연율 %'
    gdp = _fred("A191RL1Q225SBEA", api_key, 1)
    if gdp and (pub := _fresh(gdp[0], today)):
        add("A191RL1Q225SBEA", "GDP", _quarter(gdp[0]["date"]), pub,
            [f"실질 GDP 전분기 대비 연율 {_sign(float(gdp[0]['value']))}%"], gdp[0])

    return out


# ── 금리 결정 ───────────────────────────────────────────────

def _decision(before: float, after: float) -> str:
    d = round(after - before, 2)
    if d == 0:
        return "동결"
    return f"{abs(d):.2f}%p {'인상' if d > 0 else '인하'}"


def fomc(today: date, api_key: str) -> list[Result]:
    out: list[Result] = []
    for s in FOMC_DECISION_DAYS:
        us_day = date.fromisoformat(s)
        kst = _ny_to_kst(us_day, 14, 0).date()
        if kst > today or (today - kst).days > LOOKBACK_DAYS:
            continue
        lo = _fred("DFEDTARL", api_key, 15)
        hi = _fred("DFEDTARU", api_key, 15)
        # FRED 는 새 목표범위를 결정 '다음 날'부터 기록한다. 결정일 당일 값은 옛 범위다.
        # (9/16 인상을 결정일 당일과 전날로 비교했다가 '동결'로 잘못 적을 뻔했다.)
        # 그래서 결정일을 사이에 두고 '엄격히 전'과 '엄격히 뒤'를 비교한다.
        after = [(o, h) for o, h in zip(lo, hi) if o["date"] > s and o["date"] == h["date"]]
        before = [(o, h) for o, h in zip(lo, hi) if o["date"] < s and o["date"] == h["date"]]
        r = Result(f"fomc:{s}", "rate", "US", "FOMC 금리 결정", "",
                   announced=_label(kst), source="FRED DFEDTARL·DFEDTARU")
        if not after or not before:
            if (today - kst).days <= PENDING_DAYS:
                r.pending = True
                out.append(r)
            continue
        (al, ah), (bl, bh) = after[-1], before[0]       # 결정 직후 값, 직전 값
        a_lo, a_hi, b_hi = float(al["value"]), float(ah["value"]), float(bh["value"])
        r.lines.append(f"기준금리 목표범위 {a_lo:.2f}~{a_hi:.2f}% · {_decision(b_hi, a_hi)}")
        out.append(r)
    return out


def bok(today: date) -> list[Result]:
    out: list[Result] = []
    for s in BOK_DECISION_DAYS:
        d = date.fromisoformat(s)
        if d > today or (today - d).days > LOOKBACK_DAYS:
            continue
        r = Result(f"bok:{s}", "rate", "KR", "한국은행 금통위 금리 결정", "",
                   announced=_label(d), source="한국은행 ECOS")
        with db.connect() as c:
            # FOMC 와 같은 이유로 결정일 당일은 비교에서 뺀다 — 적용일이 당일이든
            # 다음 날이든, 엄격히 전·뒤를 비교하면 둘 다 맞게 나온다.
            aft = c.execute("SELECT close FROM observations WHERE instrument='KR_BASE' "
                            "AND trade_date>? ORDER BY trade_date LIMIT 1", (s,)).fetchone()
            bef = c.execute("SELECT close FROM observations WHERE instrument='KR_BASE' "
                            "AND trade_date<? ORDER BY trade_date DESC LIMIT 1", (s,)).fetchone()
        if not aft or not bef:
            if (today - d).days <= PENDING_DAYS:
                r.pending = True
                out.append(r)
            continue
        r.lines.append(f"기준금리 {aft['close']:.2f}% · {_decision(bef['close'], aft['close'])}")
        out.append(r)
    return out


# ── 묶기 ────────────────────────────────────────────────────

def recent(today: date) -> tuple[list[Result], list[str]]:
    """오늘 실을 결과와, 가져오지 못한 출처 목록.

    이미 다른 날 실었던 결과는 뺀다. 같은 날 다시 만든 경우에는 다시 싣는다.
    '집계 전' 항목은 수치가 들어오면 그때 다시 실어야 하므로 기록하지 않는다.
    """
    env = _load_env()
    shown = _shown()
    items: list[Result] = []
    missing: list[str] = []

    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    watch = {t: t for t in (cfg.get("watchlist", {}) or {}).get("us", []) or []}

    jobs = []
    if env.get("FINNHUB_API_KEY"):
        jobs.append(("미국 실적", lambda: earnings(today, env["FINNHUB_API_KEY"],
                                                   {**MAJOR_US, **watch})))
    if env.get("FRED_API_KEY"):
        jobs.append(("미국 경제지표", lambda: macro(today, env["FRED_API_KEY"])))
        jobs.append(("FOMC", lambda: fomc(today, env["FRED_API_KEY"])))
    jobs.append(("한국은행", lambda: bok(today)))

    for name, fn in jobs:
        try:
            items += fn()
        except Exception:                                        # noqa: BLE001
            missing.append(name)

    items = [r for r in items
             if r.pending or shown.get(r.key) in (None, today.isoformat())]
    # 금리 결정 → 지표 → 실적 순. 같은 종류 안에서는 발표일 순.
    order = {"rate": 0, "macro": 1, "earnings": 2}
    items.sort(key=lambda r: (order[r.kind], r.pending, r.announced, r.title))
    return items, missing


def _shown() -> dict[str, str]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("results_shown", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def mark_shown(state: dict, items: list[Result] | list[dict], today: date) -> dict:
    """실은 결과를 state 에 기록하고, 오래된 기록은 지운다. 새 dict 를 돌려준다."""
    shown = dict(state.get("results_shown", {}) or {})
    for r in items:
        r = r if isinstance(r, dict) else asdict(r)
        if not r.get("pending"):
            shown.setdefault(r["key"], today.isoformat())
    cutoff = (today - timedelta(days=KEEP_SHOWN_DAYS)).isoformat()
    shown = {k: v for k, v in shown.items() if v >= cutoff}
    return {**state, "results_shown": shown}


if __name__ == "__main__":
    from brief.clock import today_kst
    t = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else today_kst()
    got, miss = recent(t)
    print(f"{t} 기준 지난 일정 결과 {len(got)}건" + (f" · 못 가져옴: {miss}" if miss else ""))
    for r in got:
        tag = "집계 전" if r.pending else ""
        print(f"\n[{r.announced}] {r.title} {r.period} {tag}")
        for line in r.lines:
            print(f"   {line}")
        print(f"   ({r.source})")
