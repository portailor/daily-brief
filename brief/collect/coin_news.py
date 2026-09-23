"""해외 코인 뉴스 — 키 없이 받는 RSS 와 이미 쓰는 Finnhub 키로 모은다.

  출처   CoinDesk · Cointelegraph · The Block · Decrypt (RSS, 키 없음)
         Finnhub crypto 뉴스 (기존 FINNHUB_API_KEY — 위 매체 기사를 모아 준다)
  기간   최근 48시간

두 가지로 쓴다.
  눈여겨볼 코인  급등 코인·대표 코인마다, 제목에 그 코인 이름(또는 3글자 이상 심볼)이
                 들어간 기사. '오른 이유'라고 말하지 않는다 — 같은 시간대에 그 코인을
                 다룬 기사일 뿐, 가격을 움직였는지는 기사만으로 알 수 없다.
  기관·ETF      제목에 ETF·기관 매수·보유 같은 단어가 들어간 기사. 고르는 단어 목록은
                 INSTITUTIONAL 에 있고 화면에도 기준을 적는다.

기사 제목은 영어 그대로 싣는다 — 번역기를 거치면 뜻이 바뀔 수 있다.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (daily-brief; personal news digest)"}
RSS = [("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
       ("Cointelegraph", "https://cointelegraph.com/rss"),
       ("The Block", "https://www.theblock.co/rss.xml"),
       ("Decrypt", "https://decrypt.co/feed")]
WINDOW = timedelta(hours=48)
PER_COIN = 3
INSTITUTIONAL = re.compile(
    r"\b(ETFs?|treasury|treasuries|institution(al|s)?|endowment|pension|sovereign|"
    r"13F|holdings?|stake|buys|bought|acquires?|acquired|accumulat\w*|reserve|"
    r"BlackRock|Fidelity|Vanguard|MicroStrategy|Strategy Inc|Harvard|Yale)\b", re.I)
# 이름이 다른 코인 이름 안에 들어가는 경우 — '비트코인' 기사를 고를 때 'Bitcoin Cash' 는 뺀다
SHADOWS = {"bitcoin": ["bitcoin cash", "bitcoin sv", "wrapped bitcoin"],
           "ethereum": ["ethereum classic"]}


def _rss(source: str, url: str) -> list[dict]:
    res = requests.get(url, headers=UA, timeout=20)
    res.raise_for_status()
    out = []
    for it in ET.fromstring(res.content).findall(".//item"):
        title, link, pub = it.findtext("title"), it.findtext("link"), it.findtext("pubDate")
        if not (title and link and pub):
            continue
        out.append({"title": title.strip(), "url": link.strip(), "source": source,
                    "at": parsedate_to_datetime(pub).astimezone(KST)})
    return out


def _finnhub() -> list[dict]:
    from brief.collect.macro import _load_env
    key = _load_env().get("FINNHUB_API_KEY", "")
    if not key:
        return []
    res = requests.get("https://finnhub.io/api/v1/news", params={"category": "crypto", "token": key},
                       timeout=20)
    res.raise_for_status()
    return [{"title": a["headline"].strip(), "url": a["url"], "source": a.get("source") or "Finnhub",
             "at": datetime.fromtimestamp(a["datetime"], timezone.utc).astimezone(KST)}
            for a in res.json() if a.get("headline") and a.get("url")]


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def articles(now: datetime) -> tuple[list[dict], list[str]]:
    """48시간 안의 기사, 같은 제목은 하나로. 최신순."""
    got, missing = [], []
    for source, url in RSS:
        try:
            got += _rss(source, url)
        except Exception as exc:                                  # noqa: BLE001
            missing.append(f"{source}({type(exc).__name__})")
    try:
        got += _finnhub()
    except Exception as exc:                                      # noqa: BLE001
        missing.append(f"Finnhub({type(exc).__name__})")
    # 같은 소식을 여러 매체가 쓰면 제목 낱말이 절반 넘게 겹친다 — 가장 먼저 본 것 하나만 둔다
    # (9/23: 'Binance takes $100M stake in Circle' 이 네 매체에서 네 줄로 나왔다)
    kept: list[set] = []
    out = []
    for a in sorted(got, key=lambda a: a["at"], reverse=True):
        if now - a["at"] > WINDOW or not a["url"].startswith(("http://", "https://")):
            continue
        words = set(_norm(a["title"]).split())
        if any(len(words & k) / max(1, len(words | k)) >= 0.4 for k in kept):
            continue
        kept.append(words)
        out.append(a)
    return out, missing


def mentions(a: dict, cid: str, name: str, sym: str) -> bool:
    t = a["title"]
    low = t.lower()
    for s in SHADOWS.get(cid, []):
        low = low.replace(s, " ")
    if re.search(rf"\b{re.escape(name.lower())}\b", low):
        return True
    # 심볼은 대문자 그대로, 3글자 이상만 (ONE·GAS 같은 흔한 낱말과 헷갈리지 않게 대문자만 본다)
    return len(sym) >= 3 and re.search(rf"(?<![A-Za-z]){re.escape(sym.upper())}(?![A-Za-z])", t) is not None


def _view(a: dict, now: datetime) -> dict:
    ago = now - a["at"]
    h = int(ago.total_seconds() // 3600)
    when = f"{max(1, int(ago.total_seconds() // 60))}분 전" if h < 1 else f"{h}시간 전"
    return {"title": a["title"], "url": a["url"], "source": a["source"], "when": when}


def collect(coins: list[dict], now: datetime | None = None) -> dict:
    """coins: [{'id','name','sym','ko'(선택),'why'}] — why 는 목록에 오른 까닭(급등/대표)."""
    now = now or datetime.now(KST)
    arts, missing = articles(now)
    watch = []
    for c in coins:
        hits = [a for a in arts if mentions(a, c["id"], c["name"], c["sym"])]
        watch.append({**c, "news": [_view(a, now) for a in hits[:PER_COIN]], "news_n": len(hits)})
    inst = [_view(a, now) for a in arts if INSTITUTIONAL.search(a["title"])][:6]
    return {"watch": watch, "institutional": inst, "total": len(arts),
            "sources": sorted({a["source"] for a in arts}), "missing": missing}


if __name__ == "__main__":
    d = collect([{"id": "bitcoin", "name": "Bitcoin", "sym": "BTC", "why": "대표"},
                 {"id": "ethereum", "name": "Ethereum", "sym": "ETH", "why": "대표"},
                 {"id": "bitcoin-cash", "name": "Bitcoin Cash", "sym": "BCH", "why": "급등"}])
    print("기사", d["total"], "출처", d["sources"], "못 받음", d["missing"])
    for w in d["watch"]:
        print(f"\n{w['name']} ({w['news_n']}건)")
        for n in w["news"]:
            print(f"  [{n['source']} {n['when']}] {n['title'][:90]}")
    print("\n기관·ETF")
    for n in d["institutional"]:
        print(f"  [{n['source']} {n['when']}] {n['title'][:90]}")
