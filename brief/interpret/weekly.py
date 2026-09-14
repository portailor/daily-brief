"""월요일 아침 주간 정리.

일·월요일 아침에는 새로 마감된 거래가 없다. 같은 일일 브리핑을 반복하는 대신
월요일에 지난 한 주를 묶어 보고, 이번 주 일정으로 한 주를 준비하게 한다.

여기도 전부 계산이다. '지난주 분위기' 같은 해석은 붙이지 않는다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from brief.clock import label                                   # noqa: E402
from brief.collect.market import Instrument                     # noqa: E402

# 주간 표에 올릴 지표. 한국 투자자가 월요일 아침에 확인하는 순서.
KEY_IDS = ["KOSPI", "KOSDAQ", "USDKRW", "KTB3Y", "SPX", "NASDAQ",
           "US10Y", "VIX", "WTI", "GOLD"]


@dataclass
class WeekMove:
    id: str
    name: str
    close: float
    change: float            # % 또는 bp
    kind: str
    decimals: int

    @property
    def change_text(self) -> str:
        return f"{self.change:+.0f}bp" if self.kind == "rate" else f"{self.change:+.2f}%"

    @property
    def direction(self) -> str:
        return "up" if self.change > 0 else "down" if self.change < 0 else "flat"


@dataclass
class WeekSummary:
    start: date
    end: date
    moves: list[WeekMove] = field(default_factory=list)
    top_day: dict | None = None           # 한 주 중 가장 이례적이었던 날
    flows: list[dict] = field(default_factory=list)
    pred_hit: int = 0
    pred_total: int = 0

    @property
    def period(self) -> str:
        return f"{label(self.start)} ~ {label(self.end)}"


def last_week(today: date) -> tuple[date, date]:
    """오늘 기준 직전 주의 월~금."""
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(days=7)
    return start, start + timedelta(days=4)


def summarize(conn, today: date, insts: dict[str, Instrument]) -> WeekSummary:
    start, end = last_week(today)
    s = WeekSummary(start=start, end=end)
    s_iso, e_iso = start.isoformat(), end.isoformat()

    # ── 주간 등락: 지난주 마지막 종가 vs 그 전 주 마지막 종가 ──
    for iid in KEY_IDS:
        inst = insts.get(iid)
        if not inst:
            continue
        end_row = conn.execute(
            "SELECT close FROM observations WHERE instrument=? AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT 1", (iid, e_iso)).fetchone()
        base_row = conn.execute(
            "SELECT close FROM observations WHERE instrument=? AND trade_date<? "
            "ORDER BY trade_date DESC LIMIT 1", (iid, s_iso)).fetchone()
        if not end_row or not base_row or not base_row["close"]:
            continue
        c1, c0 = end_row["close"], base_row["close"]
        chg = (c1 - c0) * 100 if inst.kind == "rate" else (c1 / c0 - 1) * 100
        s.moves.append(WeekMove(iid, inst.name, c1, chg, inst.kind, inst.decimals))

    # ── 한 주 중 가장 이례적이었던 하루 ──
    predictable = [i for i, v in insts.items() if v.predict]
    if predictable:
        ph = ",".join("?" * len(predictable))
        row = conn.execute(
            f"SELECT instrument, trade_date, sigma, chg_pct, chg_bp FROM metrics "
            f"WHERE trade_date BETWEEN ? AND ? AND sigma IS NOT NULL "
            f"AND instrument IN ({ph}) ORDER BY ABS(sigma) DESC LIMIT 1",
            (s_iso, e_iso, *predictable)).fetchone()
        if row:
            inst = insts[row["instrument"]]
            move = (f"{row['chg_bp']:+.0f}bp" if inst.kind == "rate"
                    else f"{row['chg_pct']:+.2f}%")
            s.top_day = {"name": inst.name, "day": label(row["trade_date"]),
                         "move": move, "sigma": row["sigma"]}

    # ── 외국인 주간 누적 ──
    for market in ("KOSPI", "KOSDAQ"):
        rows = conn.execute(
            "SELECT net_buy FROM flows WHERE market=? AND investor='외국인합계' "
            "AND trade_date BETWEEN ? AND ?", (market, s_iso, e_iso)).fetchall()
        if rows:
            vals = [r["net_buy"] for r in rows]
            s.flows.append({
                "market": market,
                "total_eok": sum(vals) / 100_000_000,
                "sell_days": sum(1 for v in vals if v < 0),
                "days": len(vals),
            })

    # ── 지난주에 채점된 예측 ──
    row = conn.execute(
        "SELECT SUM(result='hit') h, COUNT(*) n FROM predictions "
        "WHERE result IN ('hit','miss') AND resolved_on BETWEEN ? AND ?",
        (s_iso, e_iso)).fetchone()
    s.pred_hit, s.pred_total = (row["h"] or 0), (row["n"] or 0)

    return s
