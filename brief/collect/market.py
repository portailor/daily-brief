"""yfinance 시세 수집기.

원칙: 여기서는 아무것도 해석하지 않는다. 받은 숫자를 그대로 저장만 한다.
한 종목이 실패해도 나머지는 계속 수집하고, 실패 목록을 리포트에 남긴다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from brief import db  # noqa: E402
from brief.clock import is_finished_session, today_kst  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"


@dataclass
class Instrument:
    id: str
    name: str
    kind: str          # price | rate
    group: str
    ticker: str = ""           # yfinance 전용
    source: str = "yfinance"   # yfinance | fred | ecos | krx
    unit: str = ""
    decimals: int = 2
    # 정책금리처럼 회의 때만 바뀌는 값은 매일 오르내리는 시장 지표가 아니다.
    # σ가 튀어도 '오늘 볼 것'이 아니고, 예측 대상으로 삼으면 채점만 오염된다.
    predict: bool = True
    # 원자재 선물은 '최근월물'을 이어 붙인 가격이라, 만기가 다가와 다음 월물로
    # 넘어가는 날 실제 거래 없이도 두 계약의 가격 차이만큼 가짜 변동이 찍힌다.
    # 그래서 표에는 보여주되, 자동으로 만들어지는 문장(결론·오늘 볼 것·예측·
    # 주간 이례일)의 근거로는 쓰지 않는다. 거짓 문장이 나갈 여지를 없애기 위해서다.
    futures: bool = False

    @property
    def can_claim(self) -> bool:
        """이 지표의 움직임을 근거로 문장을 만들어도 되는가."""
        return self.predict and not self.futures


@dataclass
class CollectReport:
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    stale: dict[str, str] = field(default_factory=dict)   # 마지막 거래일이 오래된 것
    rows_written: int = 0

    def summary(self) -> str:
        lines = [f"수집 성공 {len(self.ok)}건 / 실패 {len(self.failed)}건 / {self.rows_written} rows"]
        if self.failed:
            lines.append("  실패: " + ", ".join(f"{k}({v})" for k, v in self.failed.items()))
        if self.stale:
            lines.append("  지연: " + ", ".join(f"{k}={v}" for k, v in self.stale.items()))
        return "\n".join(lines)


def load_instruments(path: Path = CONFIG, source: str | None = None) -> list[Instrument]:
    """설정의 지표 목록. source를 주면 그 경로로 수집하는 것만 돌려준다."""
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = [Instrument(**row) for row in cfg["instruments"]]
    return [i for i in items if i.source == source] if source else items


def fetch_one(inst: Instrument, period: str = "2y") -> pd.DataFrame:
    """yfinance에서 한 종목의 일봉을 받아온다. 빈 응답은 예외로 올린다."""
    import yfinance as yf

    hist = yf.Ticker(inst.ticker).history(period=period, auto_adjust=False)
    if hist.empty:
        raise ValueError("빈 응답")

    hist = hist.reset_index()
    date_col = "Date" if "Date" in hist.columns else hist.columns[0]
    out = pd.DataFrame({
        "trade_date": pd.to_datetime(hist[date_col]).dt.strftime("%Y-%m-%d"),
        "open": hist.get("Open"),
        "high": hist.get("High"),
        "low": hist.get("Low"),
        "close": hist.get("Close"),
        "volume": hist.get("Volume"),
    })
    return out.dropna(subset=["close"])


def collect(instruments: list[Instrument] | None = None,
            period: str = "2y",
            stale_days: int = 5) -> CollectReport:
    # yfinance로 받을 수 있는 것만. 국고채·기준금리 같은 건 macro.py 담당이다.
    instruments = instruments or load_instruments(source="yfinance")
    report = CollectReport()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = today_kst()

    with db.session() as conn:
        for inst in instruments:
            try:
                frame = fetch_one(inst, period)
            except Exception as exc:                      # noqa: BLE001
                report.failed[inst.id] = type(exc).__name__
                continue

            # 장중 값(오늘 날짜)과 주말 날짜는 버린다. brief/clock.py 참고.
            frame = frame[frame["trade_date"].map(is_finished_session)]
            if frame.empty:
                report.failed[inst.id] = "마감 데이터 없음"
                continue

            rows = [
                (r.trade_date, inst.id, r.open, r.high, r.low, r.close,
                 r.volume, "yfinance", now)
                for r in frame.itertuples()
            ]
            conn.executemany(
                """INSERT INTO observations
                   (trade_date, instrument, open, high, low, close, volume, source, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(trade_date, instrument) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume,
                     source=excluded.source, fetched_at=excluded.fetched_at""",
                rows,
            )
            report.ok.append(inst.id)
            report.rows_written += len(rows)

            last = frame["trade_date"].iloc[-1]
            gap = (today - datetime.strptime(last, "%Y-%m-%d").date()).days
            if gap > stale_days:
                report.stale[inst.id] = f"{last}({gap}일 전)"

    return report


if __name__ == "__main__":
    db.init()
    rep = collect()
    print(rep.summary())
    with db.connect() as c:
        for r in c.execute("""
            SELECT o.instrument, o.trade_date, o.close
            FROM observations o
            JOIN (SELECT instrument, MAX(trade_date) d FROM observations GROUP BY instrument) m
              ON o.instrument = m.instrument AND o.trade_date = m.d
            ORDER BY o.instrument"""):
            print(f"  {r['instrument']:<12} {r['trade_date']}  {r['close']:>12,.3f}")
