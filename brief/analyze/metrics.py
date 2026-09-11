"""파생 지표 계산. 전부 결정론적이며 LLM은 여기에 관여하지 않는다.

이 레이어가 존재하는 이유:
  "WTI가 1.4% 내렸다"만으로는 큰 움직임인지 노이즈인지 알 수 없다.
  σ(평소 변동폭 대비 몇 배인가)와 52주 밴드 내 위치가 있어야
  '오늘 진짜 봐야 할 지표'를 사람 판단 없이 골라낼 수 있다.

금리(kind=rate)는 %가 아니라 bp로 다룬다.
4.83% → 4.94% 는 +2.3% 가 아니라 +11bp 이고, 시장은 후자로 말한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from brief import db  # noqa: E402
from brief.collect.market import Instrument, load_instruments  # noqa: E402


def _streak(changes: pd.Series) -> pd.Series:
    """같은 방향 연속일. +3 = 3일 연속 상승, -2 = 2일 연속 하락."""
    sign = np.sign(changes.fillna(0)).astype(int)
    out, run = [], 0
    prev = 0
    for s in sign:
        if s == 0:
            run = 0
        elif s == prev:
            run += s
        else:
            run = s
        out.append(run)
        prev = s
    return pd.Series(out, index=changes.index)


def compute(frame: pd.DataFrame, inst: Instrument, cfg: dict) -> pd.DataFrame:
    """observations 한 종목치를 받아 metrics 행들을 만든다."""
    f = frame.sort_values("trade_date").reset_index(drop=True)
    close = f["close"]

    sig_win = cfg["sigma_window"]
    rng_win = cfg["range_window"]

    out = pd.DataFrame({"trade_date": f["trade_date"], "instrument": inst.id})
    out["chg"] = close.diff()

    if inst.kind == "rate":
        # 금리: bp 단위. 변동성도 bp 기준으로 잰다.
        out["chg_bp"] = out["chg"] * 100
        out["chg_pct"] = np.nan
        basis = out["chg_bp"]
    else:
        out["chg_pct"] = close.pct_change() * 100
        out["chg_bp"] = np.nan
        basis = out["chg_pct"]

    # σ: 오늘 움직임 ÷ 최근 sig_win일 변동의 표준편차.
    #    오늘 값이 표준편차에 섞이지 않도록 shift(1)로 과거만 사용한다.
    vol = basis.rolling(sig_win, min_periods=20).std().shift(1)
    out["sigma"] = (basis / vol).replace([np.inf, -np.inf], np.nan)

    hi = close.rolling(rng_win, min_periods=60).max()
    lo = close.rolling(rng_win, min_periods=60).min()
    span = (hi - lo).replace(0, np.nan)
    out["high_52w"] = hi
    out["low_52w"] = lo
    out["pct_52w"] = ((close - lo) / span * 100).clip(0, 100)

    for w in cfg["ma_windows"]:
        out[f"ma{w}"] = close.rolling(w, min_periods=max(5, w // 4)).mean()

    out["vs_ma20"] = (close / out["ma20"] - 1) * 100
    out["vs_ma200"] = (close / out["ma200"] - 1) * 100
    out["streak"] = _streak(out["chg"])

    return out


def run() -> int:
    import yaml
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["analysis"]
    instruments = {i.id: i for i in load_instruments()}

    written = 0
    cols = ["trade_date", "instrument", "chg", "chg_pct", "chg_bp", "sigma",
            "pct_52w", "high_52w", "low_52w", "ma20", "ma60", "ma200",
            "vs_ma20", "vs_ma200", "streak"]

    with db.session() as conn:
        for iid, inst in instruments.items():
            raw = pd.read_sql_query(
                "SELECT trade_date, close FROM observations "
                "WHERE instrument = ? ORDER BY trade_date",
                conn, params=(iid,))
            if len(raw) < 20:
                continue

            m = compute(raw, inst, cfg)[cols]
            m = m.replace({np.nan: None})
            conn.executemany(
                f"""INSERT INTO metrics ({','.join(cols)})
                    VALUES ({','.join('?' * len(cols))})
                    ON CONFLICT(trade_date, instrument) DO UPDATE SET
                    {','.join(f'{c}=excluded.{c}' for c in cols[2:])}""",
                list(m.itertuples(index=False, name=None)))
            written += len(m)
    return written


def notable(conn, trade_date: str, cfg: dict) -> list[dict]:
    """오늘 σ가 임계치를 넘은 지표만 골라 σ 절댓값 내림차순으로 반환."""
    rows = conn.execute(
        "SELECT * FROM metrics WHERE trade_date = ? AND sigma IS NOT NULL",
        (trade_date,)).fetchall()
    out = [dict(r) for r in rows if abs(r["sigma"]) >= cfg["sigma_notable"]]
    return sorted(out, key=lambda r: -abs(r["sigma"]))


if __name__ == "__main__":
    n = run()
    print(f"metrics 계산 완료: {n} rows\n")

    import yaml
    cfg = yaml.safe_load((Path("config/settings.yaml")).read_text(encoding="utf-8"))
    insts = {i.id: i for i in load_instruments()}

    with db.connect() as c:
        latest = c.execute("SELECT MAX(trade_date) d FROM metrics").fetchone()["d"]
        print(f"=== 최근일 {latest} 전체 지표 ===")
        hdr = f"{'지표':<14}{'종가':>12}{'변화':>10}{'σ':>7}{'52주위치':>9}{'200일선':>9}{'연속':>6}"
        print(hdr); print("-" * len(hdr.encode('utf-8').decode('utf-8')) )
        for r in c.execute(
                "SELECT m.*, o.close FROM metrics m JOIN observations o "
                "ON m.trade_date=o.trade_date AND m.instrument=o.instrument "
                "WHERE m.trade_date = ? ORDER BY ABS(COALESCE(m.sigma,0)) DESC", (latest,)):
            i = insts[r["instrument"]]
            chg = f"{r['chg_bp']:+.0f}bp" if i.kind == "rate" else f"{r['chg_pct']:+.2f}%"
            sig = f"{r['sigma']:+.2f}" if r["sigma"] is not None else "  -  "
            p52 = f"{r['pct_52w']:.0f}%" if r["pct_52w"] is not None else "-"
            vma = f"{r['vs_ma200']:+.1f}%" if r["vs_ma200"] is not None else "-"
            print(f"{i.name:<14}{r['close']:>12,.2f}{chg:>10}{sig:>7}{p52:>9}{vma:>9}{r['streak']:>6}")

        print(f"\n=== σ {cfg['analysis']['sigma_notable']} 이상 = 오늘 볼 것 ===")
        nb = notable(c, latest, cfg["analysis"])
        if not nb:
            print("  없음 — 전 지표가 평소 변동폭 안. '오늘은 특별한 게 없다'가 정답인 날.")
        for r in nb:
            print(f"  {insts[r['instrument']].name:<14} σ={r['sigma']:+.2f}")
