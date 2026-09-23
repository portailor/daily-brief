"""코인 탭 — 공개 시세만 모은다. 키가 필요 없다.

  해외 시세   CoinGecko — 가격(달러), 24시간·7일 변동, 시가총액, 전체 시장
              (무료 데모 키 COINGECKO_API_KEY 가 있으면 붙인다. 없어도 동작)
              (2026-09-22 대조: 코인베이스·크라켄 체결가와 0.04% 이내)
  국내 시세   업비트 — 원화 가격, 24시간 거래대금
  심리 지표   alternative.me 공포·탐욕 지수 (0~100)
  30일 흐름   CoinGecko market_chart — 비트코인·이더리움 하루 한 점(달러)
  급등 코인   시가총액 상위 250개 중 24시간 상승률 순 (스테이블·토큰화 자산 제외).
              시총 하한을 두는 이유: 이름 모를 초소형 코인이 몇백 % 뛰는 건 소식이 아니다.

변동률 기준은 하나로 맞춘다: 모두 '24시간 전 대비'(CoinGecko).
업비트 자체 등락률은 매일 오전 9시 기준가 대비라 같은 순간에도 다른 숫자가 된다
(9/22 16시 BTC: CoinGecko +4.39%, 업비트 -1.98%). 둘을 섞으면 헷갈리므로
업비트는 원화 가격과 김치 프리미엄 계산에만 쓴다.

코인은 24시간 거래되므로 '마감' 이 없다. 모든 값에 수집 시각을 붙인다.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.retry import with_retry  # noqa: E402

OUT = ROOT / "data" / "crypto.json"
KST = timezone(timedelta(hours=9))

CG = "https://api.coingecko.com/api/v3"
UPBIT = "https://api.upbit.com/v1"
FNG = "https://api.alternative.me/fng/"

# 대표 코인 — CoinGecko id, 업비트 마켓, 한글 이름
MAJORS = [
    ("bitcoin", "KRW-BTC", "비트코인"),
    ("ethereum", "KRW-ETH", "이더리움"),
    ("ripple", "KRW-XRP", "리플(XRP)"),
    ("solana", "KRW-SOL", "솔라나"),
]
# 30일 그래프를 그릴 코인
HISTORY = [("bitcoin", "비트코인"), ("ethereum", "이더리움")]
GAINER_UNIVERSE = 250      # 급등 코인을 고르는 범위 — 시가총액 상위 몇 개 안에서
# 가격이 1달러에 묶이도록 설계된 코인. 시가총액 순위에는 두되 '스테이블'로 표시한다.
STABLE = {"usdt", "usdc", "dai", "fdusd", "usde", "tusd", "pyusd", "usds"}

FNG_KO = {"Extreme Fear": "극단적 공포", "Fear": "공포", "Neutral": "중립",
          "Greed": "탐욕", "Extreme Greed": "극단적 탐욕"}


def _get(url: str, **params):
    headers = {"accept": "application/json"}
    # CoinGecko 무료 데모 키. 없으면 키 없는 공개 호출로 간다 — 여러 사람이 함께 쓰는
    # GitHub 서버에서는 공개 호출이 한도에 걸릴 수 있어 키를 붙인다.
    if url.startswith(CG):
        from brief.collect.macro import _load_env
        key = _load_env().get("COINGECKO_API_KEY", "")
        if key:
            headers["x-cg-demo-api-key"] = key
    res = with_retry(lambda: requests.get(url, params=params, timeout=25, headers=headers),
                     attempts=3, label=url.split("/")[2])
    res.raise_for_status()
    return res.json()


def _markets() -> list[dict]:
    """시가총액 상위 GAINER_UNIVERSE 개 (달러 기준). 순위표는 앞 8개, 급등은 전체에서 고른다."""
    rows = _get(f"{CG}/coins/markets", vs_currency="usd", order="market_cap_desc",
                per_page=GAINER_UNIVERSE, page=1, price_change_percentage="24h,7d")
    return [{"id": r["id"], "sym": r["symbol"].upper(), "name": r["name"],
             "rank": r.get("market_cap_rank"),
             "usd": r["current_price"], "cap": r["market_cap"],
             "chg24": r.get("price_change_percentage_24h_in_currency")
                      if r.get("price_change_percentage_24h_in_currency") is not None
                      else r.get("price_change_percentage_24h"),
             "chg7d": r.get("price_change_percentage_7d_in_currency"),
             "stable": r["symbol"].lower() in STABLE} for r in rows]


def _history(cid: str) -> list[dict]:
    """지난 30일 하루 한 점. 점은 매일 0시(UTC) = 한국시간 오전 9시 가격이고,
    마지막 점만 수집한 순간의 가격이라 날짜가 앞 점과 겹친다 → '지금'으로 적는다."""
    raw = _get(f"{CG}/coins/{cid}/market_chart", vs_currency="usd", days=30,
               interval="daily")["prices"]
    out = []
    for ms, price in raw:
        d = datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(KST)
        out.append({"d": f"{d.month}/{d.day}", "p": price})
    if out:
        out[-1]["d"] = "지금"
    return out


def collect() -> dict:
    now = datetime.now(KST)
    data: dict = {"collected_at": now.isoformat(timespec="minutes"),
                  "asof": f"{now.month}/{now.day} {now:%H:%M}",
                  "majors": [], "top": [], "gainers": [], "history": [],
                  "market": {}, "upbit_top": [],
                  "fng": None, "missing": []}

    # 해외 시세 · 시가총액 상위
    top: list[dict] = []
    try:
        top = _markets()
        # 시가총액 순위에서 스테이블코인(1달러 고정)과 토큰화 자산(심볼에 '_', 예: 주택담보
        # 대출을 토큰으로 만든 FIGR_HELOC)은 뺀다. 가격이 움직이지 않거나 코인이 아니라서
        # 순위표를 읽는 데 방해만 된다. 뺀다는 사실은 화면에 적는다.
        coins = [c for c in top if not c["stable"] and "_" not in c["sym"]]
        data["top"] = coins[:8]
        data["gainers"] = sorted([c for c in coins if (c["chg24"] or 0) > 0],
                                 key=lambda c: -c["chg24"])[:5]
    except Exception as exc:                                      # noqa: BLE001
        data["missing"].append(f"해외 시세({type(exc).__name__})")

    # 대표 코인 — 원화 환산 해외가와 업비트 가격으로 김치 프리미엄
    try:
        ids = ",".join(c[0] for c in MAJORS)
        cg_krw = _get(f"{CG}/simple/price", ids=ids, vs_currencies="krw")
        time.sleep(1.5)
        ub = {t["market"]: t for t in _get(f"{UPBIT}/ticker",
                                            markets=",".join(c[1] for c in MAJORS))}
        by_id = {c["id"]: c for c in top}
        # 김치 프리미엄에 쓴 환율을 화면에 적어, 누구나 다시 계산해 볼 수 있게 한다.
        # (9/22 대조: CoinGecko 환산 환율 1,360.39 · 실시간 원/달러 1,361.18)
        btc = cg_krw.get("bitcoin", {}).get("krw")
        if btc and by_id.get("bitcoin"):
            data["fx"] = btc / by_id["bitcoin"]["usd"]
        for cid, market, ko in MAJORS:
            g = by_id.get(cid)
            if not g:
                continue
            krw_global = (cg_krw.get(cid) or {}).get("krw")
            krw_upbit = (ub.get(market) or {}).get("trade_price")
            prem = (krw_upbit / krw_global - 1) * 100 if krw_global and krw_upbit else None
            data["majors"].append({"name": ko, "sym": g["sym"], "usd": g["usd"],
                                   "chg24": g["chg24"], "chg7d": g["chg7d"],
                                   "krw": krw_upbit, "premium": prem})
    except Exception as exc:                                      # noqa: BLE001
        data["missing"].append(f"업비트 원화 시세({type(exc).__name__})")

    # 전체 시장
    try:
        time.sleep(1.5)
        g = _get(f"{CG}/global")["data"]
        data["market"] = {"cap_usd": g["total_market_cap"]["usd"],
                          "cap_chg24": g["market_cap_change_percentage_24h_usd"],
                          "btc_dom": g["market_cap_percentage"]["btc"],
                          "eth_dom": g["market_cap_percentage"]["eth"]}
    except Exception as exc:                                      # noqa: BLE001
        data["missing"].append(f"전체 시장({type(exc).__name__})")

    # 30일 흐름
    for cid, ko in HISTORY:
        try:
            time.sleep(1.5)
            pts = _history(cid)
            if len(pts) >= 2:
                first, last = pts[0]["p"], pts[-1]["p"]
                data["history"].append({"id": cid, "name": ko, "points": pts,
                                        "chg": (last / first - 1) * 100})
        except Exception as exc:                                  # noqa: BLE001
            data["missing"].append(f"{ko} 30일 흐름({type(exc).__name__})")

    # 업비트 24시간 거래대금 상위 — 국내에서 실제로 돈이 몰린 곳
    try:
        markets = [m["market"] for m in _get(f"{UPBIT}/market/all")
                   if m["market"].startswith("KRW-")]
        tickers = []
        for i in range(0, len(markets), 100):
            tickers += _get(f"{UPBIT}/ticker", markets=",".join(markets[i:i + 100]))
            time.sleep(0.3)
        names = {m["market"]: m.get("korean_name", m["market"])
                 for m in _get(f"{UPBIT}/market/all")}
        tickers.sort(key=lambda t: -t["acc_trade_price_24h"])
        data["upbit_top"] = [{"name": names.get(t["market"], t["market"]),
                              "sym": t["market"].split("-")[1],
                              "value_eok": t["acc_trade_price_24h"] / 1e8,
                              "krw": t["trade_price"]} for t in tickers[:5]]
    except Exception as exc:                                      # noqa: BLE001
        data["missing"].append(f"업비트 거래대금({type(exc).__name__})")

    # 공포·탐욕 지수 (하루 한 번 갱신)
    try:
        f = _get(FNG, limit=2)["data"]
        cur, prev = f[0], f[1] if len(f) > 1 else None
        d = datetime.fromtimestamp(int(cur["timestamp"]), timezone.utc).astimezone(KST)
        data["fng"] = {"value": int(cur["value"]),
                       "label": FNG_KO.get(cur["value_classification"], cur["value_classification"]),
                       "prev": int(prev["value"]) if prev else None,
                       "asof": f"{d.month}/{d.day}"}
    except Exception as exc:                                      # noqa: BLE001
        data["missing"].append(f"공포·탐욕 지수({type(exc).__name__})")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def load() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


if __name__ == "__main__":
    d = collect()
    print(f"수집 {d['asof']} (한국시간)\n")
    for c in d["majors"]:
        p = f"{c['premium']:+.2f}%" if c["premium"] is not None else "—"
        print(f"  {c['name']:<10} ${c['usd']:>12,.2f}  24h {c['chg24']:+6.2f}%  7d {c['chg7d']:+6.2f}%"
              f"  업비트 {c['krw']:>14,.0f}원  김프 {p}")
    m = d["market"]
    if m:
        print(f"\n  전체 시총 ${m['cap_usd'] / 1e12:.3f}조 ({m['cap_chg24']:+.2f}%) · BTC 도미넌스 {m['btc_dom']:.1f}%")
    if d["fng"]:
        print(f"  공포·탐욕 {d['fng']['value']} ({d['fng']['label']}) · 전날 {d['fng']['prev']}")
    print("\n  시총 상위:", [(c["sym"], round(c["chg24"] or 0, 2), "S" if c["stable"] else "") for c in d["top"]])
    print("  급등:", [(c["sym"], c["rank"], round(c["chg24"], 1)) for c in d["gainers"]])
    for h in d["history"]:
        print(f"  {h['name']} 30일 {h['points'][0]['d']} {h['points'][0]['p']:,.0f} → "
              f"{h['points'][-1]['d']} {h['points'][-1]['p']:,.0f} ({h['chg']:+.1f}%, {len(h['points'])}점)")
    print("  업비트 거래대금:", [(c["name"], f"{c['value_eok']:,.0f}억") for c in d["upbit_top"]])
    print("\n못 가져온 것:", d["missing"] or "없음")
