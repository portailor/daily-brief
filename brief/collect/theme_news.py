"""주제별 경제·산업 뉴스 — 제목과 원문 링크만 모은다 (9/29 동화님).

  주제    반도체·공급망 / 관세·무역 / 금리·연준 / 환율 / 유가·에너지 / 부동산 정책
  출처    연합뉴스 경제·산업 RSS(키 없음) + 네이버 뉴스 검색(기존 NAVER 키)
  기간    최근 24시간
  고르기  제목에 주제어가 들어간 기사, 같은 소식을 여러 매체가 쓰면 하나만, 주제마다 최대 PER_THEME 개

요약하지 않는다 — 자동 요약은 틀릴 수 있다. 제목·매체·시각·원문 링크만 보여 준다.
'오늘의 경제 신호'(brief/interpret/macro_view.py) 이슈와 주제가 맞으면 그 아래에 관련 기사로도 붙는다
(이슈의 원인이라는 뜻이 아니라 같은 주제의 오늘 기사라는 뜻).
"""
from __future__ import annotations

import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.clock import KST                        # noqa: E402

OUT = ROOT / "data" / "theme_news.json"
WINDOW = timedelta(hours=24)
PER_THEME = 4
UA = {"User-Agent": "Mozilla/5.0 (daily-brief; personal news digest)"}
RSS = [("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
       ("연합뉴스", "https://www.yna.co.kr/rss/industry.xml")]
NAVER_NEWS = "https://naverapihub.apigw.ntruss.com/search/v1/news"

# (주제, 제목에서 찾는 말, 네이버 검색어)
THEMES = [
    ("반도체·공급망", r"반도체|공급망|파운드리|HBM|칩스|칩법|보조금", "반도체 공급망"),
    ("관세·무역", r"관세|무역|통상|수출규제|수출 통제|FTA|무역수지", "관세 무역"),
    ("금리·연준", r"금리|연준|Fed|FOMC|기준금리|국채|한은|통화정책", "금리 연준"),
    ("환율", r"환율|원/달러|원·달러|원달러|달러화|엔화|위안화|외환", "환율"),
    ("유가·에너지", r"유가|원유|석유|OPEC|오펙|천연가스|LNG|에너지 가격", "국제유가"),
    ("부동산 정책", r"부동산|주택|아파트|전세|청약|주담대|주택담보|공급 대책|재건축", "부동산 정책"),
]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def _rss() -> list[dict]:
    out = []
    for source, url in RSS:
        res = requests.get(url, headers=UA, timeout=20)
        res.raise_for_status()
        for it in ET.fromstring(res.content).findall(".//item"):
            title, link, pub = it.findtext("title"), it.findtext("link"), it.findtext("pubDate")
            if title and link and pub:
                out.append({"title": _clean(title), "url": link.strip(), "source": source,
                            "at": parsedate_to_datetime(pub).astimezone(KST)})
    return out


def _naver(query: str) -> list[dict]:
    from brief.collect.macro import _load_env
    env = _load_env()
    cid, sec = env.get("NAVER_CLIENT_ID", ""), env.get("NAVER_CLIENT_SECRET", "")
    if not (cid and sec):
        return []
    res = requests.get(NAVER_NEWS, params={"query": query, "display": 30, "sort": "date", "format": "json"},
                       headers={"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec}, timeout=20)
    res.raise_for_status()
    out = []
    for it in res.json().get("items", []):
        try:
            at = parsedate_to_datetime(it["pubDate"]).astimezone(KST)
        except (KeyError, TypeError, ValueError):
            continue
        url = it.get("originallink") or it.get("link") or ""
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        out.append({"title": _clean(it.get("title")), "url": url, "source": host, "at": at})
    return out


def _norm(t: str) -> set[str]:
    return set(re.sub(r"[^0-9A-Za-z가-힣]+", " ", t.lower()).split())


def collect(now: datetime | None = None) -> dict:
    now = now or datetime.now(KST)
    data: dict = {"asof": f"{now.month}/{now.day} {now:%H:%M}", "themes": [], "missing": []}
    pool: list[dict] = []
    try:
        pool += _rss()
    except Exception as exc:                                   # noqa: BLE001
        data["missing"].append(f"연합뉴스 RSS({type(exc).__name__})")
    for name, pat, query in THEMES:
        got = []
        try:
            got = _naver(query)
        except Exception as exc:                               # noqa: BLE001
            data["missing"].append(f"네이버 {name}({type(exc).__name__})")
        cands = [a for a in pool + got
                 if now - a["at"] <= WINDOW and a["url"].startswith(("http://", "https://"))
                 and re.search(pat, a["title"], re.I)]
        cands.sort(key=lambda a: a["at"], reverse=True)
        kept, picked = [], []
        for a in cands:                                        # 같은 소식은 하나만
            w = _norm(a["title"])
            if any(len(w & k) / max(1, len(w | k)) >= 0.35 for k in kept):
                continue
            kept.append(w)
            picked.append({"title": a["title"], "url": a["url"], "source": a["source"],
                           "when": f"{a['at'].month}/{a['at'].day} {a['at']:%H:%M}"})
            if len(picked) >= PER_THEME:
                break
        data["themes"].append({"name": name, "news": picked})
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
    for t in d["themes"]:
        print(f"\n[{t['name']}] {len(t['news'])}건")
        for n in t["news"]:
            print(f"  {n['when']} {n['source']:<16} {n['title'][:70]}")
    print("\n못 가져온 것:", d["missing"] or "없음")
