"""청약·실거래 — 공공데이터포털(DATA_GO_KR_KEY) 한 키로 세 서비스.

  청약 일정   한국부동산원 청약홈 분양정보 — 앞으로 7일 안에 청약 접수를 시작하는 단지
  청약 결과   한국부동산원 청약홈 경쟁률 — 최근 7일 안에 접수가 끝난 단지
  실거래      국토교통부 아파트 매매 실거래가 — 서울 25개 구

계산 규칙 (보도 수치와 대조해 정했다)
  평균 경쟁률 = 1·2순위 전체 접수 건수 ÷ 일반공급 세대 수(주택형별 한 번씩)
    9/16 마감 '올 뉴 챔피언스시티 1차': 3,641 ÷ 2,932 = 1.24대 1 — 헤럴드경제·뉴스핌
    보도와 일치. 1순위만 세면 1.01 이 나와 보도와 달랐다.
  거래량은 신고 기한(계약 후 30일)이 지난 달만 비교한다. 아직 신고가 들어오는
  중인 달을 비교하면 거래가 줄어든 것처럼 잘못 보인다.
  해제된 거래(cdealType='O')는 거래량·금액 어디에도 넣지 않는다.

'신고가'는 싣지 않는다. 같은 단지·같은 면적의 과거 최고가를 쌓아 둬야 판정할 수
있는데, 아직 그 기록이 없다.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.collect.macro import _load_env  # noqa: E402
from brief.retry import with_retry          # noqa: E402

OUT = ROOT / "data" / "housing.json"

APPLY = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
CMPET = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet"
RTMS = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"

# 서울 25개 구 법정동 코드(앞 5자리)
SEOUL_GU = {
    "11110": "종로구", "11140": "중구", "11170": "용산구", "11200": "성동구",
    "11215": "광진구", "11230": "동대문구", "11260": "중랑구", "11290": "성북구",
    "11305": "강북구", "11320": "도봉구", "11350": "노원구", "11380": "은평구",
    "11410": "서대문구", "11440": "마포구", "11470": "양천구", "11500": "강서구",
    "11530": "구로구", "11545": "금천구", "11560": "영등포구", "11590": "동작구",
    "11620": "관악구", "11650": "서초구", "11680": "강남구", "11710": "송파구",
    "11740": "강동구",
}
REPORT_DAYS = 30        # 부동산 거래 신고 기한 (계약일로부터)


def _json(url: str, **params) -> dict:
    res = with_retry(lambda: requests.get(url, params=params, timeout=30),
                     attempts=3, label=url.split("/")[-1])
    res.raise_for_status()
    return res.json()


# ── 청약 ────────────────────────────────────────────────────

def _brief(x: dict) -> dict:
    return {"name": x["HOUSE_NM"], "area": x.get("SUBSCRPT_AREA_CODE_NM", ""),
            "addr": x.get("HSSPLY_ADRES", ""), "units": x.get("TOT_SUPLY_HSHLDCO"),
            "kind": x.get("HOUSE_SECD_NM", ""), "start": x.get("RCEPT_BGNDE"),
            "end": x.get("RCEPT_ENDDE"), "announce": x.get("PRZWNER_PRESNATN_DE"),
            "url": x.get("PBLANC_URL") or "", "no": x.get("HOUSE_MANAGE_NO")}


def upcoming(key: str, today: date, days: int = 7) -> list[dict]:
    d = _json(APPLY, serviceKey=key, page=1, perPage=100,
              **{"cond[RCEPT_BGNDE::GTE]": today.isoformat(),
                 "cond[RCEPT_BGNDE::LTE]": (today + timedelta(days=days)).isoformat()})
    rows = [_brief(x) for x in d.get("data", [])]
    return sorted(rows, key=lambda r: (r["start"], -(r["units"] or 0)))


def ratio(key: str, house_no: str) -> dict | None:
    """평균 경쟁률 = 1·2순위 전체 접수 ÷ 일반공급 세대(주택형별 한 번씩)."""
    rows = _json(CMPET, serviceKey=key, page=1, perPage=1000,
                 **{"cond[HOUSE_MANAGE_NO::EQ]": house_no}).get("data", [])
    if not rows:
        return None
    supply = {r["HOUSE_TY"]: int(r["SUPLY_HSHLDCO"] or 0) for r in rows}
    req = sum(int(r["REQ_CNT"] or 0) for r in rows)
    total = sum(supply.values())
    if not total:
        return None
    # 주택형별 1순위 해당지역 경쟁률 중 가장 높은 것 (청약홈이 적은 값을 그대로 쓴다)
    best = None
    for r in rows:
        if r["SUBSCRPT_RANK_CODE"] == 1 and r["RESIDE_SENM"] == "해당지역":
            try:
                v = float(str(r["CMPET_RATE"]).replace(",", ""))
            except ValueError:
                continue
            if best is None or v > best[1]:
                t = r["HOUSE_TY"].strip()
                m = re.match(r"0*(\d+)(?:\.\d+)?\s*([A-Z]*)", t)
                best = (f"{m.group(1)}㎡{m.group(2)}" if m else t, v)
    # 1·2순위를 다 받고도 공급보다 접수가 적은 주택형 = 미달
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["HOUSE_TY"]] = by_type.get(r["HOUSE_TY"], 0) + int(r["REQ_CNT"] or 0)
    short = sum(1 for t, n in by_type.items() if n < supply[t])
    return {"req": req, "supply": total, "avg": req / total,
            "best_type": best[0] if best else None, "best": best[1] if best else None,
            "short_types": short, "types": len(supply)}


def results(key: str, today: date, days: int = 7) -> list[dict]:
    d = _json(APPLY, serviceKey=key, page=1, perPage=100,
              **{"cond[RCEPT_ENDDE::GTE]": (today - timedelta(days=days)).isoformat(),
                 "cond[RCEPT_ENDDE::LT]": today.isoformat()})
    out = []
    for x in d.get("data", []):
        b = _brief(x)
        try:
            r = ratio(key, b["no"])
        except Exception:                                         # noqa: BLE001
            r = None
        if r:
            out.append({**b, **r})
        time.sleep(0.2)
    # 공급이 큰 단지부터 — 작은 취소분 재공급이 목록을 덮지 않게
    return sorted(out, key=lambda r: -r["supply"])


# ── 실거래 (서울) ───────────────────────────────────────────

def _trades(key: str, lawd: str, ym: str) -> list[dict]:
    out, page = [], 1
    while True:
        res = with_retry(lambda: requests.get(RTMS, params={
            "serviceKey": key, "LAWD_CD": lawd, "DEAL_YMD": ym,
            "numOfRows": 1000, "pageNo": page}, timeout=40), attempts=3, label=f"실거래 {lawd}")
        res.raise_for_status()
        root = ET.fromstring(res.text)
        code = root.findtext(".//resultCode")
        if code not in ("000", "00"):
            raise RuntimeError(root.findtext(".//resultMsg") or f"실거래 오류 {code}")
        items = [{c.tag: (c.text or "").strip() for c in it} for it in root.findall(".//item")]
        out += items
        total = int(root.findtext(".//totalCount") or 0)
        if len(out) >= total or not items:
            return out
        page += 1


def _complete_month(today: date) -> date:
    """신고 기한이 모두 지난 가장 최근 달의 1일."""
    m = today.replace(day=1)
    while True:
        m = (m - timedelta(days=1)).replace(day=1)
        month_end = (m + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if today > month_end + timedelta(days=REPORT_DAYS):
            return m


def _ym(d: date) -> str:
    return d.strftime("%Y%m")


def seoul(key: str, today: date) -> dict:
    full = _complete_month(today)
    prev = (full - timedelta(days=1)).replace(day=1)
    year_ago = full.replace(year=full.year - 1)
    this_m = today.replace(day=1)
    last_m = (this_m - timedelta(days=1)).replace(day=1)

    counts = {full: 0, prev: 0, year_ago: 0}
    recent: list[dict] = []
    since = today - timedelta(days=7)

    for code, gu in SEOUL_GU.items():
        for m in counts:
            rows = _trades(key, code, _ym(m))
            counts[m] += sum(1 for r in rows if r.get("cdealType") != "O")
            time.sleep(0.15)
        for m in {this_m, last_m}:
            for r in _trades(key, code, _ym(m)):
                if r.get("cdealType") == "O" or r.get("dealingGbn") != "중개거래":
                    continue
                d = date(int(r["dealYear"]), int(r["dealMonth"]), int(r["dealDay"]))
                if d < since:
                    continue
                recent.append({"gu": gu, "dong": r.get("umdNm", ""), "apt": r["aptNm"],
                               "area": float(r["excluUseAr"]), "floor": r.get("floor", ""),
                               "man": int(r["dealAmount"].replace(",", "")), "date": d.isoformat()})
            time.sleep(0.15)

    recent.sort(key=lambda r: -r["man"])
    lab = lambda d: f"{d.year}년 {d.month}월"                       # noqa: E731
    return {"month": lab(full), "count": counts[full],
            "prev_month": lab(prev), "prev": counts[prev],
            "year_ago_month": lab(year_ago), "year_ago": counts[year_ago],
            "recent_since": since.isoformat(), "recent_top": recent[:5],
            "recent_n": len(recent)}


# ── 묶기 ────────────────────────────────────────────────────

def collect() -> dict:
    from brief.clock import today_kst
    today = today_kst()
    key = _load_env().get("DATA_GO_KR_KEY", "")
    data: dict = {"date": today.isoformat(), "upcoming": [], "results": [],
                  "seoul": None, "missing": []}
    if not key:
        data["missing"].append("청약·실거래(공공데이터포털 인증키 필요)")
    else:
        for name, fn, field in (("청약 일정", lambda: upcoming(key, today), "upcoming"),
                                ("청약 결과", lambda: results(key, today), "results"),
                                ("서울 실거래", lambda: seoul(key, today), "seoul")):
            try:
                data[field] = fn()
            except Exception as exc:                              # noqa: BLE001
                data["missing"].append(f"{name}({type(exc).__name__})")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def load() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


if __name__ == "__main__":
    t = time.time()
    d = collect()
    print(f"걸린 시간 {time.time() - t:.0f}초 · 못 가져옴 {d['missing'] or '없음'}\n")
    print("[청약 일정]")
    for u in d["upcoming"]:
        print(f"  {u['start']} {u['area']:<3} {u['name'][:28]:<28} {u['units']}세대 · 발표 {u['announce']}")
    print("\n[청약 결과]")
    for r in d["results"]:
        best = f" · 최고 {r['best_type']} {r['best']:.2f}대 1" if r["best"] else ""
        print(f"  {r['end']} {r['area']:<3} {r['name'][:26]:<26} {r['req']:,}건/{r['supply']:,}세대 = {r['avg']:.2f}대 1"
              f"{best} · 미달 {r['short_types']}/{r['types']}형")
    s = d["seoul"]
    if s:
        print(f"\n[서울 거래량] {s['month']} {s['count']:,}건 · {s['prev_month']} {s['prev']:,}건 · {s['year_ago_month']} {s['year_ago']:,}건")
        print(f"[최근 7일 큰 거래] {s['recent_n']}건 중")
        for r in s["recent_top"]:
            print(f"  {r['date']} {r['gu']} {r['apt']} {r['area']:.1f}㎡ {r['floor']}층 {r['man'] / 10000:.2f}억")
