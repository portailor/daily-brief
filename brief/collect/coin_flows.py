"""코인으로 들어오는 큰돈과 앞으로의 일정 — 동화님이 받은 무료 키 두 개(9/23).

  미국 현물 ETF   SoSoValue /etfs/summary-history — 비트코인·이더리움 ETF 전체의 하루 순유입
                  (미국 거래일 기준, 보통 다음 날 아침에 채워진다). 9/23 대조: 9/21 비트코인
                  ETF +9.99억 달러 — Decrypt·The Block '하루 약 10억 달러 유입' 기사와 일치.
  기업 매수       SoSoValue /btc-treasuries — 비트코인을 사 모으는 상장사의 최근 매수·매도 기록
  코인 일정       CoinMarketCal /v2/events — 앞으로 7일 (무료 플랜: 시총 상위 100개 코인).
                  날짜가 딱 정해진 일정만 싣는다. '이달 중'·'분기 중' 같은 어림 일정과
                  몇 주씩 이어지는 캠페인은 뺀다 — 날짜 없는 예고는 소식이 아니다.
                  무료 플랜은 제목·날짜만 준다(설명·출처 없음). 그래서 제목 낱말로 일정 '종류'를
                  가리고 종류마다 정해 둔 한국어 설명을 붙인다(EVENT_TYPES). 그 일정 하나하나를
                  설명하지는 않는다 — 제목만 보고 지어내게 된다. 종류를 모르는 일정과
                  AMA·방송·테스트넷처럼 가격과 거리가 먼 일정은 뺀다. (동화님 9/23: "코인명이랑
                  날짜만 적어두면 이게 뭔데?")

키: SOSOVALUE_API_KEY, COINMARKETCAL_API_KEY (config/.env · GitHub Secrets). 값은 찍지 않는다.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

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


# 일정 종류 — (이름, 제목에서 찾는 낱말, 설명, 대체로 가격은, 출처). 위에서부터 먼저 맞는 것 하나.
# '대체로 가격은'은 조사·연구가 있는 종류에만 쓰고 출처를 함께 단다(9/23 확인). 연구를 못 찾은
# 종류는 없다고 적는다 — 금리와 부동산처럼 '보통 이렇다'를 동화님이 원했지만 근거 없이 쓰면 환각이다.
EVENT_TYPES = [
    ("바이백·소각", r"buy ?back|burn",
     "운영 측이 시장에서 코인을 되사거나(바이백) 없애는(소각) 일. 시장에 도는 양이 줄어듭니다.",
     "발표 뒤 1주일은 시장 평균보다 나은 경우가 절반 정도였고, 한 달 뒤까지 앞선 경우는 드물었다는 분석이 있습니다.",
     ("Tokenomist 분석(11개 코인)", "https://tokenomist.ai/research/buyback-and-burn-explained-what-they-are-who-is-doing-them-and-whether-they-actually-work")),
    ("물량 해제", r"unlock|vesting",
     "묶여 있던 코인이 풀려 팔 수 있게 되는 일. 시장에 나올 수 있는 물량이 늘어납니다.",
     "대체로 가격이 내렸습니다. 1만 6천여 건을 본 조사에서 90%가 하락 압력이었고, 하락은 해제 한 달 전쯤부터 시작됐습니다.",
     ("Keyrock 조사", "https://keyrock.com/from-locked-to-liquidity-what-16000-token-unlocks-teach-us/")),
    ("상장폐지", r"delist",
     "거래소에서 그 코인 거래가 끝나는 일. 그 거래소에서는 더 사고팔 수 없게 됩니다.",
     "대체로 가격이 내렸습니다. 상장폐지 같은 나쁜 소식이 좋은 소식보다 가격을 더 크게 움직였다는 연구가 있습니다.",
     ("학술 연구(사건 연구)", "https://dergipark.org.tr/en/pub/epfad/article/1011204")),
    ("거래소 상장", r"\blisting\b|\blists?\b|\blisted\b",
     "새 거래소에서 거래가 시작되는 일. 사고팔 수 있는 곳이 늘어납니다.",
     "대체로 가격이 올랐습니다. 327건을 본 연구에서 상장 당일 평균 +5.7%, 앞뒤 사흘을 합치면 +9.2%의 초과 상승이 있었습니다.",
     ("Ante(2019) 연구", "https://www.blockchainresearchlab.org/wp-content/uploads/2019/10/Exploring-Market-Reactions-to-Exchange-Listings-of-Cryptocurrencies-BRL-working-paper3.pdf")),
    ("입출금 중단", r"withdrawal|deposit|suspen|maintenance|halt",
     "거래소가 그 코인의 입금·출금을 잠시 막는 일. 점검이나 네트워크 업그레이드 때 흔합니다.",
     "가격이 한쪽으로 움직인다는 조사는 찾지 못했습니다. 다만 그 거래소 가격만 다른 곳과 벌어질 수 있습니다.",
     None),
    ("반감기", r"halving",
     "새로 만들어지는 코인 양이 절반으로 줄어드는 일. 공급 속도가 느려집니다.",
     "비트코인은 지금까지 네 번의 반감기 뒤 1년 동안 모두 올랐지만, 사례가 네 번뿐이라 일반화하기 어렵습니다.",
     None),
    ("네트워크 업그레이드", r"mainnet|upgrade|hard ?fork|\bfork\b|activation|migration",
     "코인이 돌아가는 네트워크의 기술을 바꾸는 일. 전후로 거래소가 입출금을 잠시 멈추기도 합니다.",
     "업그레이드 자체로 가격이 한쪽으로 움직인다는 조사는 찾지 못했습니다.",
     None),
    ("에어드롭·배분", r"airdrop|distribution|snapshot",
     "코인을 보유자 등에게 나눠 주는 일. 받은 사람이 팔면 시장에 물량이 늘 수 있습니다.",
     "대체로 가격이 내렸습니다. 2024년 에어드롭 코인의 88.7%가 90일 뒤 가격이 떨어졌고, 대부분 15일 안에 급락했습니다.",
     ("Keyrock 2024 조사", "https://www.dlnews.com/articles/snapshot/keyrock-study-says-most-token-airdrops-crash-after-launch/")),
    ("보유자 투표", r"\bvote\b|voting|governance|proposal",
     "보유자 투표로 운영 방침을 정하는 일. 결과에 따라 공급량·수수료 같은 규칙이 바뀔 수 있습니다.",
     "투표 자체로 가격이 한쪽으로 움직인다는 조사는 찾지 못했습니다.",
     None),
]
# 가격과 거리가 먼 일정 — 종류가 맞아도 뺀다 (예: 'Testnet upgrade', 'Upgrade AMA')
EVENT_SKIP = r"\bAMA\b|\bcall\b|spaces|livestream|stream|meetup|webinar|conference|summit|hackathon|" \
             r"testnet|devnet|demo|pre-?release|campaign|contest|competition|giveaway|quest|podcast|interview"


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
        kind = event_kind(e["title"])
        if kind is None:
            continue
        coins = e.get("coins", [])
        # 더 알아보기 — 코인 이름과 일정 제목으로 뉴스 검색 (설명을 지어내지 않고 찾아볼 길을 준다)
        q = " ".join(filter(None, [(coins[0].get("name") if coins else ""), e["title"]]))
        out.append({"date": d.isoformat(), "label": f"{d.month}/{d.day}({'월화수목금토일'[d.weekday()]})",
                    "title": e["title"], "kind": kind[0], "about": kind[1],
                    "tendency": kind[2], "tsrc": kind[3],
                    "coins": [{"sym": c["symbol"].upper(), "name": c.get("name", "")} for c in coins],
                    "url": e.get("sourceUrl") or "",
                    "search": "https://news.google.com/search?" + urlencode({"q": q, "hl": "en-US"})})
    return out


def event_kind(title: str) -> tuple | None:
    """제목으로 일정 종류를 가린다. 가격과 거리가 먼 일정이나 모르는 종류면 None."""
    if re.search(EVENT_SKIP, title, re.I):
        return None
    for name, pat, about, tendency, src in EVENT_TYPES:
        if re.search(pat, title, re.I):
            return name, about, tendency, src
    return None


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
        print(f"  {e['label']} [{e['kind']}] {','.join(c['sym'] for c in e['coins'])} {e['title']}")
    print("못 가져온 것:", d["missing"] or "없음")
