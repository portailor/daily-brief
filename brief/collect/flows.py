"""투자자별 수급 수집 (KRX).

한국 시장에서 지수 방향을 가장 잘 설명하는 단일 지표다.
"코스피 -1.76%"보다 "외국인이 이틀 연속 2조씩 팔았다"가
훨씬 많은 것을 말해준다.

pykrx 는 import 시점에 KRX 로그인을 시도하므로,
환경변수를 먼저 채운 뒤에 import 해야 한다.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief import db                       # noqa: E402
from brief.collect.macro import _load_env  # noqa: E402
from brief.clock import is_finished_session  # noqa: E402

MARKETS = ["KOSPI", "KOSDAQ"]
INVESTORS = ["외국인합계", "기관합계", "개인"]

# 억원 단위로 환산해서 다룬다. 원 단위 숫자는 사람이 읽을 수 없다.
EOK = 100_000_000


@dataclass
class FlowReport:
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    rows: int = 0

    def summary(self) -> str:
        out = [f"수급 {len(self.ok)}개 시장 / {self.rows} rows"]
        if self.failed:
            out.append("  실패: " + ", ".join(f"{k}({v})" for k, v in self.failed.items()))
        return "\n".join(out)


def _ensure_credentials() -> bool:
    env = _load_env()
    kid, kpw = env.get("KRX_ID", ""), env.get("KRX_PW", "")
    if not kid or not kpw:
        return False
    os.environ["KRX_ID"] = kid
    os.environ["KRX_PW"] = kpw
    return True


def collect(days: int = 400) -> FlowReport:
    rep = FlowReport()
    if not _ensure_credentials():
        rep.failed["KRX"] = "자격증명 없음"
        return rep

    from pykrx import stock                       # noqa: PLC0415  (자격증명 이후)

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with db.session() as conn:
        for market in MARKETS:
            try:
                df = stock.get_market_trading_value_by_date(
                    start, end, market, etf=True, etn=True, elw=True)
                if df is None or df.empty:
                    raise ValueError("빈 응답")
            except Exception as exc:               # noqa: BLE001
                rep.failed[market] = type(exc).__name__
                continue

            batch = []
            for date, row in df.iterrows():
                d = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)
                if not is_finished_session(d):          # 장중 집계는 버린다
                    continue
                for inv in INVESTORS:
                    if inv in row and row[inv] is not None:
                        batch.append((d, market, inv, float(row[inv]), "KRX", ))

            conn.executemany(
                """INSERT INTO flows (trade_date, market, investor, net_buy, source)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(trade_date, market, investor) DO UPDATE SET
                     net_buy=excluded.net_buy, source=excluded.source""",
                batch)
            rep.ok.append(market)
            rep.rows += len(batch)

    return rep


# ─────────────────────────────────────────────────────────────
#  분석 — 연속일과 이례도
# ─────────────────────────────────────────────────────────────

@dataclass
class FlowStat:
    market: str
    investor: str
    trade_date: str
    net_buy: float            # 원
    eok: float                # 억원
    streak: int               # 같은 방향 연속일 (+매수 / -매도)
    sigma: float | None       # 평소 대비 몇 배
    ma5: float                # 최근 5일 누적(억원)

    @property
    def direction(self) -> str:
        return "순매수" if self.net_buy > 0 else "순매도"


def analyze(conn, market: str, investor: str, window: int = 60) -> FlowStat | None:
    rows = conn.execute(
        "SELECT trade_date, net_buy FROM flows "
        "WHERE market=? AND investor=? ORDER BY trade_date", (market, investor)).fetchall()
    if len(rows) < 20:
        return None

    vals = [r["net_buy"] for r in rows]
    latest, last_date = vals[-1], rows[-1]["trade_date"]

    # 연속일
    streak = 0
    sign = 1 if latest > 0 else -1
    for v in reversed(vals):
        if (v > 0) == (latest > 0) and v != 0:
            streak += 1
        else:
            break
    streak *= sign

    # 이례도: 오늘을 뺀 과거 window일의 표준편차 대비
    hist = vals[-(window + 1):-1]
    sigma = None
    if len(hist) >= 20:
        mean = sum(hist) / len(hist)
        var = sum((v - mean) ** 2 for v in hist) / len(hist)
        sd = var ** 0.5
        if sd > 0:
            sigma = (latest - mean) / sd

    return FlowStat(
        market=market, investor=investor, trade_date=last_date,
        net_buy=latest, eok=latest / EOK, streak=streak, sigma=sigma,
        ma5=sum(vals[-5:]) / EOK,
    )


def describe(stat: FlowStat) -> str:
    """수급을 한 문장으로. 숫자는 전부 계산된 값이고 해석을 덧붙이지 않는다."""
    amount = abs(stat.eok)
    unit = f"{amount / 10_000:.2f}조원" if amount >= 10_000 else f"{amount:,.0f}억원"

    parts = [f"{stat.investor.replace('합계', '')}이 {stat.market}에서 {unit} {stat.direction}"]

    if abs(stat.streak) >= 2:
        parts.append(f"{abs(stat.streak)}일 연속 {stat.direction} 중")
    if stat.sigma is not None and abs(stat.sigma) >= 1.5:
        parts.append(f"평소 하루 규모의 {abs(stat.sigma):.1f}배")

    five = abs(stat.ma5)
    five_unit = f"{five / 10_000:.2f}조원" if five >= 10_000 else f"{five:,.0f}억원"
    parts.append(f"최근 5일 누적 {five_unit} "
                 f"{'순매수' if stat.ma5 > 0 else '순매도'}")

    return " · ".join(parts) + "입니다."


if __name__ == "__main__":
    db.init()
    r = collect()
    print(r.summary())

    with db.connect() as c:
        print()
        for m in MARKETS:
            for inv in INVESTORS:
                s = analyze(c, m, inv)
                if s:
                    sg = f"σ{s.sigma:+.2f}" if s.sigma is not None else "σ  - "
                    print(f"  [{s.trade_date}] {m:<7}{inv:<7}"
                          f"{s.eok:>12,.0f}억  연속{s.streak:+3d}  {sg}")
        print()
        for m in MARKETS:
            s = analyze(c, m, "외국인합계")
            if s:
                print("  ·", describe(s))
