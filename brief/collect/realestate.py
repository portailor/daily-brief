"""부동산 탭 — 공식 통계만 모은다.

  한국 주간   한국부동산원 R-ONE '주간 아파트 가격동향' (매주 목요일 공표)
              매매·전세 가격지수 → 전주 대비 변동률을 지수에서 직접 계산한다.
              REB_API_KEY 가 있어야 한다(없으면 표본 5건만 준다).
  한국 월간   한국은행 ECOS — 주택담보대출 금리(예금은행 신규취급액 가중평균),
              미분양 주택(국가데이터처), 아파트 실거래가격지수(한국부동산원)
  미국        FRED — 30년 고정 모기지 금리(프레디맥, 주간), 주택착공,
              기존주택 판매, 케이스-실러 주택가격지수

부동산 통계는 주간·월간이라 매일 바뀌지 않는다. 그래서 모든 값에 '기준 시점'을
붙이고, 오래된 값을 오늘 것처럼 보이게 하지 않는다. 특히 ECOS 의 월간 주택가격은
석 달가량 늦게 올라온다(2026-09 에 최신이 6월) — 그래서 가격 흐름은 주간 통계로 보고
월간 가격지수는 싣지 않는다.

해석하는 문장은 쓰지 않는다. 오르면 오른 만큼, 내리면 내린 만큼 숫자로만 적는다.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.collect.macro import ECOS_URL, FRED_URL, _load_env  # noqa: E402
from brief.retry import with_retry                              # noqa: E402

OUT = ROOT / "data" / "realestate.json"
ITEMS_CACHE = ROOT / "data" / "reb_items.json"

REB = "https://www.reb.or.kr/r-one/openapi/"
# (주) 매매가격지수 — 아파트. SttsApiTbl 로 이름·주기(WK)·기관(한국부동산원) 확인함.
REB_SALE = "T244183132827305"
# 둘 다 주기 WK, 기준시점 2026.07.06=100 (인증키로 SttsApiTbl 전체 목록 조회해 확인)
REB_JEONSE = "T247713133046872"   # (주) 전세가격지수 — SttsApiTbl 목록에서 확인

KR_REGIONS = ("전국", "수도권", "서울", "지방권", "5대광역시")


# ── 공통 ────────────────────────────────────────────────────

def _item(id_, name, value, change, direction, asof, source, unit_note=""):
    return {"id": id_, "name": name, "value": value, "change": change,
            "dir": direction, "asof": asof, "source": source, "note": unit_note}


def _dir(v: float) -> str:
    return "up" if v > 0 else "down" if v < 0 else "flat"


def _ym(s: str) -> str:
    """'202607' 또는 '2026-07-01' → '2026년 7월'"""
    s = s.replace("-", "")
    return f"{s[:4]}년 {int(s[4:6])}월"


# ── 한국 주간 (R-ONE) ───────────────────────────────────────

def _reb(ep: str, key: str, **p) -> list[dict]:
    res = with_retry(lambda: requests.get(REB + ep, params={"KEY": key, "Type": "json", **p},
                                          timeout=30), attempts=2, label=f"R-ONE {ep}")
    res.raise_for_status()
    body = res.json()
    blocks = next(iter(body.values()), [])
    head = next((b["head"] for b in blocks if "head" in b), [])
    result = next((h["RESULT"] for h in head if "RESULT" in h), {})
    if result.get("CODE") not in ("INFO-000", None):
        raise RuntimeError(result.get("MESSAGE", "R-ONE 오류"))
    return next((b["row"] for b in blocks if "row" in b), [])


def _regions(key: str, statbl: str) -> dict[str, dict]:
    """지역 이름 → 분류 정보. 목록이 잘 바뀌지 않아 7일간 캐시한다."""
    cache = {}
    if ITEMS_CACHE.exists():
        cache = json.loads(ITEMS_CACHE.read_text(encoding="utf-8"))
        if cache.get(statbl, {}).get("saved", "") >= (date.today() - timedelta(days=7)).isoformat():
            return cache[statbl]["items"]
    rows = _reb("SttsApiTblItm.do", key, STATBL_ID=statbl, pSize=1000)
    items = {r["ITM_FULLNM"]: {"id": r["ITM_ID"], "name": r["ITM_NM"]}
             for r in rows if r.get("ITM_TAG") == "분류"}
    cache[statbl] = {"saved": date.today().isoformat(), "items": items}
    ITEMS_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return items


def _series(key: str, statbl: str, cls_id: int, weeks: int = 260) -> list[tuple[str, float]]:
    """최근 몇 주 지수. (공표 기준일, 지수) 오래된 것부터.

    기간은 주차(YYYYWW) 형식으로 넘겨야 한다. 날짜 형식을 넣으면 엉뚱한 주가 온다.
    5년치를 받는 이유: 'N주 연속'을 정확히 세려면 끊긴 지점까지 거슬러 가야 한다.
    2026-09-14 기준 서울 매매는 84주 연속 상승(2025-01-20 보합 이후)으로, 60주만
    받으면 58주로 잘못 셌다. 84주는 같은 주 언론 보도(부동산원 발표 인용)와 일치.
    """
    start = date.today() - timedelta(weeks=weeks)
    y, w, _ = start.isocalendar()
    rows = _reb("SttsApiTblData.do", key, STATBL_ID=statbl, DTACYCLE_CD="WK",
                CLS_ID=cls_id, START_WRTTIME=f"{y}{w:02d}", pSize=1000)
    pts = sorted({(r["WRTTIME_DESC"], float(r["DTA_VAL"])) for r in rows
                  if r.get("DTA_VAL") is not None})
    return pts


def streak_of(chgs: list[float]) -> tuple[float, int]:
    """(이번 주 변동률, 같은 방향으로 몇 주째인가).

    부동산원 발표처럼 소수 둘째 자리로 반올림해 판단하고, 0.00% 는 보합이라
    연속을 끊는다. 이번 주가 보합이면 0주다.
    """
    last = round(chgs[-1], 2)
    if last == 0:
        return 0.0, 0
    streak = 0
    for c in reversed(chgs):
        c = round(c, 2)
        if c == 0 or (c > 0) != (last > 0):
            break
        streak += 1
    return last, streak


def _weekly_block(key: str, statbl: str) -> dict | None:
    regions = _regions(key, statbl)

    def one(fullname: str, label: str) -> dict | None:
        info = regions.get(fullname)
        if not info:
            return None
        pts = _series(key, statbl, info["id"])
        if len(pts) < 2:
            return None
        chgs = [(pts[i][1] / pts[i - 1][1] - 1) * 100 for i in range(1, len(pts))]
        last, streak = streak_of(chgs)
        # 받아온 기간 전체가 같은 방향이면 그보다 길 수 있다 — '이상'으로 표시한다.
        # (처음엔 12주만 받아 '11주째'가 받아온 길이의 한계였는데 그대로 적을 뻔했다.)
        return {"name": label, "chg": last, "streak": streak,
                "streak_capped": bool(last) and streak == len(chgs),
                "date": pts[-1][0]}

    out = [r for r in (one(n, n) for n in KR_REGIONS) if r]
    if not out:
        return None

    # 서울 25개 구 — 전주 대비 가장 많이 오른 셋, 가장 적게 오른(또는 내린) 셋
    gu = []
    for full, info in regions.items():
        parts = full.split(">")
        if parts[0] == "서울" and parts[-1].endswith("구"):
            r = one(full, parts[-1])
            if r:
                gu.append(r)
            time.sleep(0.15)
    gu.sort(key=lambda r: -r["chg"])
    return {"date": out[0]["date"], "regions": out,
            "gu_top": gu[:3], "gu_bottom": gu[-3:][::-1] if len(gu) >= 6 else []}


def kr_weekly(key: str) -> dict:
    out = {"sale": _weekly_block(key, REB_SALE)}
    if REB_JEONSE:
        out["jeonse"] = _weekly_block(key, REB_JEONSE)
    return out


# ── 한국 월간 (ECOS) ────────────────────────────────────────

def _ecos(key: str, code: str, item: str, months: int = 15) -> list[tuple[str, float]]:
    end = date.today().strftime("%Y%m")
    start = (date.today() - timedelta(days=31 * months)).strftime("%Y%m")
    res = with_retry(lambda: requests.get(
        f"{ECOS_URL}/{key}/json/kr/1/100/{code}/M/{start}/{end}/{item}", timeout=30),
        attempts=3, label=f"ECOS {code}")
    rows = res.json().get("StatisticSearch", {}).get("row", [])
    return [(r["TIME"], float(r["DATA_VALUE"])) for r in rows if r.get("DATA_VALUE") not in (None, "", "-")]


def kr_monthly(key: str) -> list[dict]:
    out = []

    rate = _ecos(key, "121Y006", "BECBLA0302")
    if len(rate) >= 2:
        (t, v), (_, p) = rate[-1], rate[-2]
        d = round((v - p) * 100)
        out.append(_item("KR_MORTGAGE", "주택담보대출 금리", f"{v:.2f}%",
                         f"{d:+d}bp", _dir(d), _ym(t), "한국은행",
                         "예금은행 신규취급액 가중평균"))

    for code, name, idc in (("I410A", "미분양 주택 · 전국", "KR_UNSOLD"),
                            ("I410R", "미분양 주택 · 수도권", "KR_UNSOLD_CAP")):
        s = _ecos(key, "901Y074", code)
        if len(s) >= 2:
            (t, v), (_, p) = s[-1], s[-2]
            out.append(_item(idc, name, f"{v:,.0f}호", f"{v - p:+,.0f}호",
                             _dir(v - p), _ym(t), "국가데이터처"))
        time.sleep(0.3)

    for code, name, idc in (("200", "아파트 실거래가격지수 · 서울", "KR_TRADE_SEOUL"),
                            ("100", "아파트 실거래가격지수 · 전국", "KR_TRADE_ALL")):
        s = _ecos(key, "901Y089", code)
        if len(s) >= 2:
            (t, v), (_, p) = s[-1], s[-2]
            c = (v / p - 1) * 100
            out.append(_item(idc, name, f"{v:,.1f}", f"{c:+.2f}%", _dir(c), _ym(t),
                             "한국부동산원", "실제 거래된 가격으로 만든 지수 · 전월 대비"))
        time.sleep(0.3)
    return out


# ── 미국 (FRED) ─────────────────────────────────────────────

def _fred(key: str, sid: str, n: int = 14) -> list[tuple[str, float]]:
    time.sleep(0.7)
    res = with_retry(lambda: requests.get(FRED_URL, params={
        "series_id": sid, "api_key": key, "file_type": "json",
        "sort_order": "desc", "limit": n}, timeout=25), attempts=2, label=f"FRED {sid}")
    res.raise_for_status()
    obs = [(o["date"], float(o["value"])) for o in res.json().get("observations", [])
           if o["value"] not in (".", "")]
    return obs[::-1]


def us(key: str) -> list[dict]:
    out = []

    m = _fred(key, "MORTGAGE30US", 3)
    if len(m) >= 2:
        (t, v), (_, p) = m[-1], m[-2]
        d = round((v - p) * 100)
        dt = date.fromisoformat(t)
        out.append(_item("US_MORTGAGE30", "30년 고정 모기지 금리", f"{v:.2f}%",
                         f"{d:+d}bp", _dir(d), f"{dt.month}/{dt.day} 주", "프레디맥(FRED)"))

    h = _fred(key, "HOUST", 3)
    if len(h) >= 2:
        (t, v), (_, p) = h[-1], h[-2]
        c = (v / p - 1) * 100
        out.append(_item("US_STARTS", "주택착공", f"연율 {v / 10:,.1f}만 호",
                         f"{c:+.1f}%", _dir(c), _ym(t), "미 인구조사국(FRED)",
                         "계절조정 연율 · 전월 대비"))

    e = _fred(key, "EXHOSLUSM495S", 3)
    if len(e) >= 2:
        (t, v), (_, p) = e[-1], e[-2]
        c = (v / p - 1) * 100
        out.append(_item("US_EXISTING", "기존주택 판매", f"연율 {v / 10_000:,.0f}만 호",
                         f"{c:+.1f}%", _dir(c), _ym(t), "전미부동산협회(FRED)",
                         "계절조정 연율 · 전월 대비"))

    cs = _fred(key, "CSUSHPINSA", 14)
    if len(cs) >= 13:
        (t, v), (_, y) = cs[-1], cs[-13]
        c = (v / y - 1) * 100
        out.append(_item("US_CASESHILLER", "케이스-실러 주택가격지수", f"{v:,.1f}",
                         f"{c:+.1f}%", _dir(c), _ym(t), "S&P(FRED)", "1년 전 대비"))
    return out


# ── 묶기 ────────────────────────────────────────────────────

def collect() -> dict:
    env = _load_env()
    data: dict = {"collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "kr_weekly": None, "kr_monthly": [], "us": [], "missing": []}

    if env.get("REB_API_KEY"):
        try:
            data["kr_weekly"] = kr_weekly(env["REB_API_KEY"])
        except Exception as exc:                                  # noqa: BLE001
            data["missing"].append(f"한국 주간 아파트 가격({type(exc).__name__})")
    else:
        data["missing"].append("한국 주간 아파트 가격(한국부동산원 인증키 필요)")

    if env.get("ECOS_API_KEY"):
        try:
            data["kr_monthly"] = kr_monthly(env["ECOS_API_KEY"])
        except Exception as exc:                                  # noqa: BLE001
            data["missing"].append(f"한국 월간 통계({type(exc).__name__})")

    if env.get("FRED_API_KEY"):
        try:
            data["us"] = us(env["FRED_API_KEY"])
        except Exception as exc:                                  # noqa: BLE001
            data["missing"].append(f"미국 주택 통계({type(exc).__name__})")

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
    kw = d.get("kr_weekly") or {}
    for kind in ("sale", "jeonse"):
        b = kw.get(kind)
        if b:
            print(f"[한국 주간 {kind}] {b['date']} 기준")
            for r in b["regions"]:
                print(f"   {r['name']:<8} {r['chg']:+.2f}%  ({r['streak']}주째)")
            print("   서울 상위:", [(r['name'], r['chg']) for r in b['gu_top']])
            print("   서울 하위:", [(r['name'], r['chg']) for r in b['gu_bottom']])
    for sec in ("kr_monthly", "us"):
        print(f"\n[{sec}]")
        for i in d[sec]:
            print(f"   {i['name']:<24} {i['value']:>14} {i['change']:>8}  ({i['asof']} · {i['source']})")
    print("\n못 가져온 것:", d["missing"] or "없음")
