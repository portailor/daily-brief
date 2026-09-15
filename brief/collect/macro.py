"""거시지표 수집 — 미 연준 FRED + 한국은행 ECOS.

yfinance 대신 이쪽을 쓰는 이유:
  둘 다 발행 기관이 직접 내놓는 공식 확정값이다. 특히 국내 국고채 금리는
  yfinance에 아예 없고, 시중에 도는 값은 출처가 제각각이라 어긋난다.
  (동화님이 처음 보여준 브리핑이 10년물을 4.83%라고 썼는데 실제는 4.94%였다.
   11bp 차이는 '치솟았다'는 서술의 근거 자체를 바꾼다.)

수집한 값은 observations 에 같은 형태로 들어가므로,
분석·채점 레이어는 출처가 어디든 신경 쓰지 않는다.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief import db  # noqa: E402
from brief.retry import with_retry  # noqa: E402
from brief.clock import is_finished_session  # noqa: E402

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch"


def _load_env() -> dict[str, str]:
    """config/.env 를 읽는다. 외부 의존 없이 단순 파싱."""
    env: dict[str, str] = {}
    path = ROOT / "config" / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("FRED_API_KEY", "ECOS_API_KEY", "DART_API_KEY",
              "FINNHUB_API_KEY", "KRX_ID", "KRX_PW",
              "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
        if os.getenv(k):
            env[k] = os.environ[k]
    return env


# ── FRED ────────────────────────────────────────────────────
# 여기 담긴 것들은 yfinance로는 못 구하거나, 구해도 부정확한 값들이다.
# 월간 계열은 '매월 1일'로 날짜가 찍혀 주말일 수 있으므로 평일 필터를 걸지 않는다.
MONTHLY = {"CPI_YOY", "UNRATE"}
FRED_SERIES = {
    "US02Y":    ("DGS2",     "미 국채 2년물",      "rate"),
    "US10Y":    ("DGS10",    "미 국채 10년물",     "rate"),
    "SPREAD_10_2": ("T10Y2Y", "장단기 금리차(10년-2년)", "rate"),
    "CPI_YOY":  ("CPIAUCSL", "미 소비자물가지수",   "level"),
    "UNRATE":   ("UNRATE",   "미 실업률",          "rate"),
    "FEDFUNDS": ("DFF",      "미 기준금리(실효)",   "rate"),
}

# ── ECOS ────────────────────────────────────────────────────
# (통계표코드, 주기, 항목코드) — 한국은행이 확정 고시하는 값
ECOS_SERIES = {
    "KR_BASE":  ("722Y001", "D", "0101000", "한국 기준금리",   "rate"),
    "KTB3Y":    ("817Y002", "D", "010200000", "국고채 3년물",  "rate"),
    "KTB10Y":   ("817Y002", "D", "010210000", "국고채 10년물", "rate"),
    "CD91":     ("817Y002", "D", "010502000", "CD 91일물",    "rate"),
}


@dataclass
class MacroReport:
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    rows: int = 0

    def summary(self) -> str:
        parts = [f"거시지표 {len(self.ok)}건 수집 / {self.rows} rows"]
        if self.failed:
            parts.append("  실패: " + ", ".join(f"{k}({v})" for k, v in self.failed.items()))
        if self.skipped:
            parts.append("  건너뜀: " + ", ".join(self.skipped))
        return "\n".join(parts)


def fetch_fred(series_id: str, api_key: str, start: str) -> list[tuple[str, float]]:
    res = with_retry(lambda: requests.get(FRED_URL, params={
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
    }, timeout=25), attempts=2, label=f"FRED {series_id}")
    res.raise_for_status()

    out = []
    for o in res.json().get("observations", []):
        if o["value"] in (".", "", None):        # FRED는 결측을 "."으로 준다
            continue
        try:
            out.append((o["date"], float(o["value"])))
        except ValueError:
            continue
    return out


def fetch_ecos(stat_code: str, cycle: str, item: str,
               api_key: str, start: str, end: str) -> list[tuple[str, float]]:
    url = (f"{ECOS_URL}/{api_key}/json/kr/1/10000/"
           f"{stat_code}/{cycle}/{start}/{end}/{item}")
    res = with_retry(lambda: requests.get(url, timeout=25),
                     attempts=2, label=f"ECOS {stat_code}")
    res.raise_for_status()
    body = res.json()

    if "RESULT" in body:                          # 에러는 RESULT 키로 온다
        raise RuntimeError(body["RESULT"].get("MESSAGE", "ECOS 오류"))

    rows = body.get("StatisticSearch", {}).get("row", [])
    out = []
    for r in rows:
        d = r.get("TIME", "")
        v = r.get("DATA_VALUE")
        if not d or v in (None, "", "-"):
            continue
        if len(d) == 8:
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        try:
            out.append((d, float(v)))
        except ValueError:
            continue
    return out


def collect(years: int = 2) -> MacroReport:
    env = _load_env()
    rep = MacroReport()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    start_d = (datetime.now() - timedelta(days=365 * years + 40)).date()
    start_iso = start_d.strftime("%Y-%m-%d")
    start_raw = start_d.strftime("%Y%m%d")
    end_raw = datetime.now().strftime("%Y%m%d")

    with db.session() as conn:
        # FRED
        fred_key = env.get("FRED_API_KEY", "")
        if not fred_key:
            rep.skipped.append("FRED(키 없음)")
        else:
            for iid, (sid, _name, _kind) in FRED_SERIES.items():
                try:
                    data = fetch_fred(sid, fred_key, start_iso)
                    if iid not in MONTHLY:
                        data = [(d, v) for d, v in data if is_finished_session(d)]
                    if not data:
                        raise ValueError("빈 응답")
                except Exception as exc:                      # noqa: BLE001
                    rep.failed[iid] = f"{type(exc).__name__}"
                    continue
                conn.executemany(
                    """INSERT INTO observations
                       (trade_date, instrument, close, source, fetched_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(trade_date, instrument) DO UPDATE SET
                         close=excluded.close, source=excluded.source,
                         fetched_at=excluded.fetched_at""",
                    [(d, iid, v, f"FRED:{sid}", now) for d, v in data])
                rep.ok.append(iid)
                rep.rows += len(data)

        # ECOS
        ecos_key = env.get("ECOS_API_KEY", "")
        if not ecos_key:
            rep.skipped.append("ECOS(키 없음)")
        else:
            for iid, (stat, cyc, item, _name, _kind) in ECOS_SERIES.items():
                try:
                    data = fetch_ecos(stat, cyc, item, ecos_key, start_raw, end_raw)
                    # 기준금리는 달력 기준으로 매일(주말 포함) 값이 찍힌다.
                    if cyc == "D":
                        data = [(d, v) for d, v in data if is_finished_session(d)]
                    if not data:
                        raise ValueError("빈 응답")
                except Exception as exc:                      # noqa: BLE001
                    rep.failed[iid] = f"{type(exc).__name__}: {str(exc)[:40]}"
                    continue
                conn.executemany(
                    """INSERT INTO observations
                       (trade_date, instrument, close, source, fetched_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(trade_date, instrument) DO UPDATE SET
                         close=excluded.close, source=excluded.source,
                         fetched_at=excluded.fetched_at""",
                    [(d, iid, v, f"ECOS:{stat}", now) for d, v in data])
                rep.ok.append(iid)
                rep.rows += len(data)

    return rep


if __name__ == "__main__":
    db.init()
    r = collect()
    print(r.summary())

    with db.connect() as c:
        print("\n=== 최신값 ===")
        ids = list(FRED_SERIES) + list(ECOS_SERIES)
        names = {**{k: v[1] for k, v in FRED_SERIES.items()},
                 **{k: v[3] for k, v in ECOS_SERIES.items()}}
        for iid in ids:
            row = c.execute(
                "SELECT trade_date, close FROM observations WHERE instrument=? "
                "ORDER BY trade_date DESC LIMIT 1", (iid,)).fetchone()
            if row:
                print(f"  {names[iid]:<22} {row['trade_date']}  {row['close']:>10,.3f}")
            else:
                print(f"  {names[iid]:<22} (없음)")
