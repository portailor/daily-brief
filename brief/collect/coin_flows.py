"""코인으로 들어오는 큰돈과 앞으로의 일정 — 동화님이 받은 무료 키 두 개(9/23).

  미국 현물 ETF   SoSoValue /etfs/summary-history — 비트코인·이더리움 ETF 전체의 하루 순유입
                  (미국 거래일 기준, 보통 다음 날 아침에 채워진다). 9/23 대조: 9/21 비트코인
                  ETF +9.99억 달러 — Decrypt·The Block '하루 약 10억 달러 유입' 기사와 일치.
  기업 매수       SoSoValue /btc-treasuries — 비트코인을 사 모으는 상장사의 최근 매수·매도 기록
  코인 일정       CoinMarketCal /v2/events — 앞으로 7일 (무료 플랜: 시총 상위 100개 코인).
                  날짜가 딱 정해진 일정만 싣는다. '이달 중'·'분기 중' 같은 어림 일정과
                  몇 주씩 이어지는 캠페인은 뺀다 — 날짜 없는 예고는 소식이 아니다.

키: SOSOVALUE_API_KEY, COINMARKETCAL_API_KEY (config/.env · GitHub Secrets). 값은 찍지 않는다.
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.retry import with_retry  # noqa: E402

KST = timezone(timedelta(hours=9))
SOSO = "https://openapi.sosovalue.com/openapi/v1"
CMCAL = "https://api.coinmarketcal.com/v2/events"
ETF_DAYS = 10              # 막대로 보일 최근 거래일 수
TREASURY_SCAN = 20         # 매수 기록을 볼 회사 수 (목록 앞쪽 = 보유량 큰 회사)
TREASURY_DAYS = 14         # 이 기간 안의 매수·매도만
SOSO_GAP = 3.2             # 분당 20회 한도


def _keys() -> dict[str, str]:
    from brief.collect.macro import _load_env
    env = _load_env()
    return {k: env.get(k, "") for k in ("SOSOVALUE_API_KEY", "COINMARKETCAL_API_KEY")}


def _soso(key: str, path: str, **params):
    res = with_retry(lambda: requests.get(f"{SOSO}{path}", params=params, timeout=25,
                                          headers={"x-soso-api-key": key}),
                     attempts=3, label="sosovalue")
    res.raise_for_status()
    body = res.json()
    if body.get("code") != 0:
        raise RuntimeError(f"SoSoValue {body.get('code')} {body.get('message')}")
    return body["data"]


def etf(key: str) -> list[dict]:
    out = []
    for sym, ko in (("BTC", "비트코인"), ("ETH", "이더리움")):
        rows = _soso(key, "/etfs/summary-history", symbol=sym, country_code="US", limit=ETF_DAYS)
        days = [{"date": r["date"], "flow": float(r["total_net_inflow"])} for r in rows][::-1]
        last = rows[0]
        streak = 0
        for r in rows:                                  # 최신부터 같은 부호가 이어진 날 수
            f = float(r["total_net_inflow"])
            if streak == 0 or (f > 0) == (streak > 0):
                streak += 1 if f > 0 else -1
            else:
                break
        out.append({"sym": sym, "name": ko, "date": last["date"],
                    "flow": float(last["total_net_inflow"]),
                    "flow5": sum(float(r["total_net_inflow"]) for r in rows[:5]),
                    "assets": float(last["total_net_assets"]),
                    "cum": float(last["cum_net_inflow"]), "streak": streak, "days": days})
        time.sleep(SOSO_GAP)
    return out


def treasuries(key: str, today: date) -> list[dict]:
    companies = _soso(key, "/btc-treasuries")[:TREASURY_SCAN]
    since = (today - timedelta(days=TREASURY_DAYS)).isoformat()
    out = []
    for c in companies:
        time.sleep(SOSO_GAP)
        try:
            hist = _soso(key, f"/btc-treasuries/{c['ticker']}/purchase-history", limit=5)
        except Exception:                                         # noqa: BLE001
            continue
        for h in hist:
            acq = float(h.get("btc_acq") or 0)
            if h["date"] < since or not acq:
                continue
            cost = float(h["acq_cost"]) if h.get("acq_cost") not in (None, "") else None
            out.append({"name": c["name"], "ticker": c["ticker"], "where": c.get("list_location", ""),
                        "date": h["date"], "btc": acq, "cost": cost,
                        # 평균 단가는 응답 필드(avg_btc_cost)가 단위가 맞지 않아 직접 나눈다
                        "price": abs(cost / acq) if cost else None,
                        "holding": float(h["btc_holding"]) if h.get("btc_holding") else None})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def events(key: str, today: date) -> list[dict]:
    res = with_retry(lambda: requests.get(CMCAL, params={"sortBy": "date_asc", "limit": 100}, timeout=25,
                                          headers={"x-api-key": key, "Accept": "application/json"}),
                     attempts=3, label="coinmarketcal")
    res.raise_for_status()
    end = today + timedelta(days=7)
    out = []
    for e in res.json().get("data", []):
        d = date.fromisoformat(e["date"][:10])
        if e.get("dateType") != "date" or e.get("isEstimated") or e.get("dateEnd"):
            continue
        if not (today <= d <= end):
            continue
        out.append({"date": d.isoformat(), "label": f"{d.month}/{d.day}({'월화수목금토일'[d.weekday()]})",
                    "title": e["title"], "coins": [c["symbol"].upper() for c in e.get("coins", [])],
                    "url": e.get("sourceUrl") or ""})
    return out


def collect(today: date | None = None) -> dict:
    today = today or datetime.now(KST).date()
    keys = _keys()
    data: dict = {"etf": [], "treasury": [], "events": [], "missing": []}
    if keys["SOSOVALUE_API_KEY"]:
        try:
            data["etf"] = etf(keys["SOSOVALUE_API_KEY"])
        except Exception as exc:                                  # noqa: BLE001
            data["missing"].append(f"ETF 자금 흐름({type(exc).__name__})")
        try:
            data["treasury"] = treasuries(keys["SOSOVALUE_API_KEY"], today)
        except Exception as exc:                                  # noqa: BLE001
            data["missing"].append(f"기업 비트코인 매수({type(exc).__name__})")
    if keys["COINMARKETCAL_API_KEY"]:
        try:
            data["events"] = events(keys["COINMARKETCAL_API_KEY"], today)
        except Exception as exc:                                  # noqa: BLE001
            data["missing"].append(f"코인 일정({type(exc).__name__})")
    return data


if __name__ == "__main__":
    d = collect()
    for e in d["etf"]:
        print(f"{e['name']} ETF {e['date']} {e['flow'] / 1e8:+.2f}억$ · 5일 {e['flow5'] / 1e8:+.1f}억$ · "
              f"순자산 {e['assets'] / 1e8:,.0f}억$ · 연속 {e['streak']}")
    for t in d["treasury"]:
        print(f"  {t['date']} {t['name']} {t['btc']:+,.0f} BTC ${(t['cost'] or 0) / 1e6:,.1f}M 보유 {t['holding']:,.0f}")
    for e in d["events"]:
        print(f"  {e['label']} {','.join(e['coins'])} {e['title']}")
    print("못 가져온 것:", d["missing"] or "없음")
