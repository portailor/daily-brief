"""종목·업종 단위 시장 상세 — 매일 실제로 쓸 수 있는 정보.

지수 몇 개만으로는 "무엇이 움직였나"를 알 수 없다. 여기서는 그날 마감된
거래를 종목·업종 단위로 풀어 저장한다. 해석은 붙이지 않고 숫자만 모은다.

  국내 (KRX 공식)   시가총액 상위 종목 · 업종별 등락 · 외국인/기관 순매수 상위 종목
                   · 거래대금이 큰 종목 중 급등락
  미국 (yfinance)   대형 기술주 · S&P500 섹터 ETF

결과는 data/detail.json 에 저장하고, 리포트는 그 파일만 읽는다.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief import db                                  # noqa: E402
from brief.clock import is_finished_session           # noqa: E402
from brief.collect.flows import _ensure_credentials   # noqa: E402

DETAIL_PATH = ROOT / "data" / "detail.json"
EOK = 100_000_000

# KRX 업종 지수 이름 (코스피 시장). 규모별·코스피200 파생 지수는 빼고 산업 분류만 쓴다.
KOSPI_SECTORS = [
    "음식료·담배", "섬유·의류", "종이·목재", "화학", "제약", "비금속", "금속",
    "기계·장비", "전기전자", "의료·정밀기기", "운송장비·부품", "유통", "전기·가스",
    "건설", "운송·창고", "통신", "금융", "증권", "보험", "일반서비스",
    "오락·문화", "IT 서비스", "부동산",
]

US_BIG = {
    "NVDA": "엔비디아", "MSFT": "마이크로소프트", "AAPL": "애플", "GOOGL": "알파벳",
    "AMZN": "아마존", "META": "메타", "AVGO": "브로드컴", "TSLA": "테슬라",
}
US_SECTORS = {
    "XLK": "기술", "XLC": "커뮤니케이션", "XLY": "경기소비재", "XLF": "금융",
    "XLV": "헬스케어", "XLI": "산업재", "XLP": "필수소비재", "XLE": "에너지",
    "XLU": "유틸리티", "XLB": "소재", "XLRE": "부동산", "SOXX": "반도체",
}

MOVER_MIN_TRADING_VALUE = 500 * EOK     # 급등락 종목은 거래대금 500억원 이상만 (소형주 잡음 제외)


def _latest_kr_date(conn) -> str | None:
    row = conn.execute(
        "SELECT MAX(trade_date) d FROM observations WHERE instrument='KOSPI'").fetchone()
    return row["d"] if row else None


def _names(stock, tickers) -> dict[str, str]:
    out = {}
    for t in tickers:
        try:
            out[t] = stock.get_market_ticker_name(t)
        except Exception:                               # noqa: BLE001
            out[t] = t
    return out


def collect_kr(kr_date: str) -> dict:
    if not _ensure_credentials():
        return {"error": "KRX 자격증명 없음"}
    from pykrx import stock                             # noqa: PLC0415

    d = kr_date.replace("-", "")
    out: dict = {"date": kr_date}

    # ── 종목 시세 (코스피·코스닥) ────────────────────────────
    frames = {}
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_ohlcv(d, market=market)
            df = df[df["거래량"] > 0]
            frames[market] = df
        except Exception as exc:                        # noqa: BLE001
            out.setdefault("errors", []).append(f"{market} 시세: {type(exc).__name__}")

    # 시가총액 상위
    for market, key, n in (("KOSPI", "kospi_top", 10), ("KOSDAQ", "kosdaq_top", 5)):
        df = frames.get(market)
        if df is None or df.empty:
            continue
        top = df.sort_values("시가총액", ascending=False).head(n)
        names = _names(stock, top.index)
        out[key] = [{
            "name": names[t], "code": t, "close": int(r["종가"]),
            "chg_pct": float(r["등락률"]), "value_eok": float(r["거래대금"] / EOK),
            "cap_jo": float(r["시가총액"] / EOK / 10_000),
        } for t, r in top.iterrows()]

    # 거래대금이 큰 종목 중 급등락
    import pandas as pd
    both = pd.concat([f.assign(시장=m) for m, f in frames.items()]) if frames else None
    if both is not None and not both.empty:
        liquid = both[both["거래대금"] >= MOVER_MIN_TRADING_VALUE]
        gain = liquid.sort_values("등락률", ascending=False).head(5)
        loss = liquid.sort_values("등락률").head(5)
        names = _names(stock, list(gain.index) + list(loss.index))
        pack = lambda frame: [{                          # noqa: E731
            "name": names[t], "market": r["시장"], "close": int(r["종가"]),
            "chg_pct": float(r["등락률"]), "value_eok": float(r["거래대금"] / EOK),
        } for t, r in frame.iterrows()]
        out["gainers"], out["losers"] = pack(gain), pack(loss)
        out["mover_min_eok"] = MOVER_MIN_TRADING_VALUE / EOK

        # 시장 전체 오른 종목 / 내린 종목 수
        out["breadth"] = {m: {"up": int((f["등락률"] > 0).sum()),
                              "down": int((f["등락률"] < 0).sum()),
                              "flat": int((f["등락률"] == 0).sum())}
                          for m, f in frames.items()}

    # ── 업종별 등락 (코스피) ─────────────────────────────────
    try:
        ic = stock.get_index_price_change(d, d, "KOSPI")
        rows = [{"name": n, "chg_pct": float(ic.loc[n, "등락률"]),
                 "value_eok": float(ic.loc[n, "거래대금"] / EOK)}
                for n in KOSPI_SECTORS if n in ic.index]
        out["sectors"] = sorted(rows, key=lambda r: -r["chg_pct"])
    except Exception as exc:                            # noqa: BLE001
        out.setdefault("errors", []).append(f"업종: {type(exc).__name__}")

    # ── 투자자별 순매수 상위 종목 (코스피+코스닥 합산) ───────────
    for investor, key in (("외국인", "foreign"), ("기관합계", "institution")):
        try:
            parts = []
            for market in ("KOSPI", "KOSDAQ"):
                df = stock.get_market_net_purchases_of_equities(d, d, market, investor)
                parts.append(df.assign(시장=market))
            df = pd.concat(parts)
            buy = df.sort_values("순매수거래대금", ascending=False).head(5)
            sell = df.sort_values("순매수거래대금").head(5)
            chg = both["등락률"] if both is not None else None
            pack = lambda frame: [{                      # noqa: E731
                "name": r["종목명"], "market": r["시장"],
                "net_eok": float(r["순매수거래대금"] / EOK),
                "chg_pct": (float(chg.loc[t]) if chg is not None and t in chg.index else None),
            } for t, r in frame.iterrows()]
            out[key] = {"buy": pack(buy), "sell": pack(sell)}
        except Exception as exc:                        # noqa: BLE001
            out.setdefault("errors", []).append(f"{investor} 순매수: {type(exc).__name__}")

    return out


def collect_us() -> dict:
    import yfinance as yf

    tickers = list(US_BIG) + list(US_SECTORS)
    raw = yf.download(" ".join(tickers), period="15d", auto_adjust=False,
                      progress=False)["Close"]
    raw.index = [i.date() if hasattr(i, "date") else i for i in raw.index]
    raw = raw[[is_finished_session(d) for d in raw.index]]
    if len(raw) < 2:
        return {"error": "미국 데이터 부족"}

    last, prev = raw.iloc[-1], raw.iloc[-2]
    us_date = raw.index[-1]

    def pack(mapping):
        rows = []
        for t, name in mapping.items():
            if t in last and prev.get(t) and last.get(t) == last.get(t):
                rows.append({"name": name, "ticker": t, "close": float(last[t]),
                             "chg_pct": float((last[t] / prev[t] - 1) * 100)})
        return rows

    return {"date": us_date.isoformat() if isinstance(us_date, date) else str(us_date),
            "big": sorted(pack(US_BIG), key=lambda r: -r["chg_pct"]),
            "sectors": sorted(pack(US_SECTORS), key=lambda r: -r["chg_pct"])}


def collect() -> dict:
    with db.connect() as conn:
        kr_date = _latest_kr_date(conn)

    detail: dict = {"fetched_at": datetime.now().isoformat(timespec="seconds")}
    if kr_date:
        try:
            detail["kr"] = collect_kr(kr_date)
        except Exception as exc:                        # noqa: BLE001
            detail["kr"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        detail["us"] = collect_us()
    except Exception as exc:                            # noqa: BLE001
        detail["us"] = {"error": f"{type(exc).__name__}: {exc}"}

    DETAIL_PATH.parent.mkdir(parents=True, exist_ok=True)
    DETAIL_PATH.write_text(json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8")
    return detail


def load() -> dict:
    try:
        return json.loads(DETAIL_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


if __name__ == "__main__":
    d = collect()
    kr, us = d.get("kr", {}), d.get("us", {})
    print("국내 기준일", kr.get("date"), "| 오류", kr.get("errors") or kr.get("error"))
    for r in kr.get("kospi_top", [])[:5]:
        print(f"  {r['name']:<10} {r['close']:>10,} {r['chg_pct']:+6.2f}%")
    print("업종 최고/최저:", kr.get("sectors", [{}])[0], kr.get("sectors", [{}])[-1])
    print("외국인 순매수 1위:", kr.get("foreign", {}).get("buy", [{}])[0])
    print("외국인 순매도 1위:", kr.get("foreign", {}).get("sell", [{}])[0])
    print("급등:", [(g['name'], g['chg_pct']) for g in kr.get("gainers", [])])
    print("미국 기준일", us.get("date"), us.get("error", ""))
    for r in us.get("big", []):
        print(f"  {r['name']:<10} {r['chg_pct']:+6.2f}%")
