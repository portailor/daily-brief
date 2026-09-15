"""크게 오른·내린 종목의 같은 날 공시·뉴스.

원칙: 원인을 단정하지 않는다. "이 공시 때문에 올랐다"는 판단은 넣지 않고,
그 종목에 대해 같은 시기에 나온 공시 제목과 뉴스 헤드라인을 원문 링크와 함께
보여줄 뿐이다. 문장을 새로 쓰지 않으므로 지어낼 여지가 없다.

  공시   금융감독원 DART (공식)          DART_API_KEY
  뉴스   NAVER API HUB 뉴스 검색 (헤드라인만)  NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
         네이버 검색 API 는 네이버 클라우드 플랫폼의 NAVER API HUB 로 옮겨졌다.
         (api.ncloud-docs.com/docs/naver-api-hub-search-news, 하루 25,000회)

뉴스 본문은 가져오지 않는다. 제목과 링크만 쓴다.
"""
from __future__ import annotations

import html
import io
import json
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.clock import KST                        # noqa: E402
from brief.collect.macro import _load_env          # noqa: E402
from brief.retry import with_retry                 # noqa: E402

CORP_CACHE = ROOT / "data" / "corp_codes.json"
DART_LIST = "https://opendart.fss.or.kr/api/list.json"
DART_CORP = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
NAVER_NEWS = "https://naverapihub.apigw.ntruss.com/search/v1/news"

# 주가와 거의 무관한 정기 보고. 목록을 흐리므로 뺀다.
ROUTINE = ("임원ㆍ주요주주특정증권등소유상황보고서", "임원·주요주주특정증권등소유상황보고서",
           "특수관계인에대한", "증권발행실적보고서")


def _corp_codes(api_key: str) -> dict[str, str]:
    """종목코드 → DART 고유번호. 7일간 캐시한다."""
    if CORP_CACHE.exists() and time.time() - CORP_CACHE.stat().st_mtime < 7 * 86400:
        return json.loads(CORP_CACHE.read_text(encoding="utf-8"))

    res = with_retry(lambda: requests.get(DART_CORP, params={"crtfc_key": api_key}, timeout=60),
                     attempts=2, label="DART 고유번호")
    res.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(res.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    mapping = {}
    for c in root.iter("list"):
        sc = (c.findtext("stock_code") or "").strip()
        if sc:
            mapping[sc] = c.findtext("corp_code")
    CORP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CORP_CACHE.write_text(json.dumps(mapping), encoding="utf-8")
    return mapping


def disclosures(api_key: str, corp_code: str, trade_day: date) -> list[dict]:
    """거래일과 그 직전 사흘 동안 나온 공시. 최근 것부터."""
    params = {"crtfc_key": api_key, "corp_code": corp_code,
              "bgn_de": (trade_day - timedelta(days=3)).strftime("%Y%m%d"),
              "end_de": trade_day.strftime("%Y%m%d"), "page_count": 20}
    body = with_retry(lambda: requests.get(DART_LIST, params=params, timeout=30),
                      attempts=2, label="DART 공시").json()
    if body.get("status") not in ("000", "013"):          # 013 = 조회 결과 없음
        raise RuntimeError(body.get("message", "DART 오류"))

    out = []
    for it in body.get("list", []):
        title = re.sub(r"\s+", " ", it.get("report_nm", "")).strip()
        if any(r in title.replace(" ", "") for r in ROUTINE):
            continue
        d = it["rcept_dt"]
        out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "title": title,
                    "url": DART_VIEW.format(it["rcept_no"])})
    return out[:3]


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def headlines(client_id: str, secret: str, name: str, trade_day: date) -> list[dict]:
    """거래일 전날~당일(한국시간)에 나온 기사 제목. 종목명이 제목에 들어간 것만."""
    res = with_retry(lambda: requests.get(
        NAVER_NEWS, params={"query": f"{name} 주가", "display": 30, "sort": "date",
                            "format": "json"},
        headers={"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": secret},
        timeout=20), attempts=2, label="네이버 뉴스")
    res.raise_for_status()

    lo, hi = trade_day - timedelta(days=1), trade_day
    out, seen = [], set()
    for it in res.json().get("items", []):
        title = _clean(it.get("title"))
        try:
            pub = parsedate_to_datetime(it["pubDate"]).astimezone(KST)
        except (KeyError, TypeError, ValueError):
            continue
        # 종목명이 제목에 없는 기사는 검색어가 우연히 걸린 것일 수 있어 뺀다
        if not (lo <= pub.date() <= hi) or name not in title or title in seen:
            continue
        seen.add(title)
        out.append({"time": pub.strftime("%m/%d %H:%M"), "title": title,
                    "url": it.get("originallink") or it.get("link")})
    return out[:3]


def attach(detail: dict) -> dict:
    """detail['kr'] 의 급등·급락 종목에 공시·뉴스를 붙인다. 실패해도 브리핑은 계속된다."""
    kr = detail.get("kr") or {}
    movers = [("up", s) for s in kr.get("gainers", [])] + [("down", s) for s in kr.get("losers", [])]
    if not movers or not kr.get("date"):
        return detail

    env = _load_env()
    dart_key = env.get("DART_API_KEY", "")
    nid, nsec = env.get("NAVER_CLIENT_ID", ""), env.get("NAVER_CLIENT_SECRET", "")
    trade_day = date.fromisoformat(kr["date"])

    corp_map: dict[str, str] = {}
    problems: list[str] = []
    if dart_key:
        try:
            corp_map = _corp_codes(dart_key)
        except Exception as exc:                            # noqa: BLE001
            problems.append(f"DART 고유번호({type(exc).__name__})")

    issues = []
    for side, s in movers:
        item = {"side": side, "name": s["name"], "market": s.get("market"),
                "chg_pct": s["chg_pct"], "disclosures": [], "news": []}
        code = s.get("code")
        if dart_key and code in corp_map:
            try:
                item["disclosures"] = disclosures(dart_key, corp_map[code], trade_day)
            except Exception as exc:                        # noqa: BLE001
                problems.append(f"{s['name']} 공시({type(exc).__name__})")
        if nid and nsec:
            try:
                item["news"] = headlines(nid, nsec, s["name"], trade_day)
            except Exception as exc:                        # noqa: BLE001
                problems.append(f"{s['name']} 뉴스({type(exc).__name__})")
        issues.append(item)

    kr["issues"] = issues
    kr["issues_sources"] = {"dart": bool(dart_key), "naver": bool(nid and nsec)}
    if problems:
        kr.setdefault("errors", []).extend(problems[:5])
    return detail


if __name__ == "__main__":
    from brief.collect import detail as detail_mod
    d = attach(detail_mod.load())
    for it in d.get("kr", {}).get("issues", []):
        print(f"{'▲' if it['side'] == 'up' else '▼'} {it['name']} {it['chg_pct']:+.2f}%")
        for x in it["disclosures"]:
            print(f"    [공시 {x['date']}] {x['title']}")
        for x in it["news"]:
            print(f"    [뉴스 {x['time']}] {x['title']}")
    print("출처:", d.get("kr", {}).get("issues_sources"), "| 오류:", d.get("kr", {}).get("errors"))
