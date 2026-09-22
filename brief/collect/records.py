"""서울 아파트 신고가 — 같은 단지·같은 전용면적의 과거 최고가를 넘은 거래.

신고가를 판정하려면 과거 최고가 기록이 있어야 한다. 그래서 두 층으로 나눈다.

  확정 기록  data/seoul_highs.json (커밋) — '지지난달'까지의 최고가.
             신고 기한(30일)이 지나 더 들어오거나 바뀔 일이 거의 없는 달만 넣는다.
             처음 한 번 36개월을 채우고, 이후엔 달이 넘어갈 때마다 한 달씩 더한다.
  최근 거래  지난달·이번 달 — 매일 새로 받아 쓴다. 신고가 들어오는 중이고 해제될 수도
             있어서 기록에 박아 두지 않는다. 해제되면 다음 날 자연히 빠진다.

판정 규칙
  - 같은 구·같은 동·같은 단지 이름·같은 전용면적(소수 첫째 자리)을 한 종류로 본다.
  - 해제된 거래와 직거래는 기록에도, 판정에도 넣지 않는다. 직거래는 가족 간 거래처럼
    시세와 동떨어진 값이 섞일 수 있다.
  - 확정 기록에 그 종류가 없으면(처음 보는 거래) 신고가로 치지 않는다.
  - 최근 두 달 안에서 먼저 더 비싸게 팔린 거래가 있으면 그것을 넘어야 한다.

기준 기간: 2006년 1월(국토부 실거래 공개 시작)부터 — 직방의 신고가 분석과 같은 기준.
처음엔 36개월만 채웠는데, 그러면 2021년 고점보다 싼 거래를 신고가라고 할 수 있었다.
앞쪽 기간은 fill_back() 으로 채운다. 화면에도 '2006년 이후 최고가'라고 적는다.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

STORE = ROOT / "data" / "seoul_highs.json"
BACKFILL_MONTHS = 36
# 신고가 기준 시작. 직방 분석처럼 국토부 실거래 공개(2006년)부터의 역대 최고가로 판정한다.
# 36개월만 보면 2021년 고점보다 싼 거래를 신고가라고 할 수 있었다.
HISTORY_START = date(2006, 1, 1)


def kind_key(r: dict, sgg: str) -> str:
    return f"{sgg}|{r.get('umdNm', '')}|{r['aptNm']}|{float(r['excluUseAr']):.1f}"


def usable(r: dict) -> bool:
    return r.get("cdealType") != "O" and r.get("dealingGbn") == "중개거래"


def man(r: dict) -> int:
    return int(r["dealAmount"].replace(",", ""))


def deal_date(r: dict) -> str:
    return date(int(r["dealYear"]), int(r["dealMonth"]), int(r["dealDay"])).isoformat()


def _month_add(m: date, n: int) -> date:
    y, mo = divmod(m.year * 12 + (m.month - 1) + n, 12)
    return date(y, mo + 1, 1)


def load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"since": None, "through": None, "max": {}}


def save(store: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    # 한 줄 한 종류 — 매달 한 번 늘어날 때 변경분이 알아보기 쉽게
    lines = ",\n".join(f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v)}"
                       for k, v in sorted(store["max"].items()))
    STORE.write_text(
        "{\n"
        f'"since": {json.dumps(store["since"])},\n'
        f'"through": {json.dumps(store["through"])},\n'
        f'"complete": {json.dumps(bool(store.get("complete")))},\n'
        f'"back_done": {json.dumps(store.get("back_done"))},\n'
        f'"max": {{\n{lines}\n}}\n}}\n', encoding="utf-8")


def fold(store: dict, fetch, codes: dict[str, str], upto: date, log=print,
         max_months: int | None = None, workers: int = 1) -> int:
    """upto(그달 1일)까지 확정 기록을 채운다. 새로 넣은 달 수를 돌려준다.

    fetch(code, 'YYYYMM') -> 그 구·그 달의 거래 목록.
    max_months — 한 번에 채울 최대 달 수. 매일 실행은 1로 부른다: 달이 넘어간
      직후 한 달만 채우면 되고, 처음 36개월은 scripts/backfill_highs.py 로 따로 채운다.
      (매일 실행이 36개월을 채우려 들면 아침 발송 시각을 넘긴다.)
    workers — 한 달 안의 25개 구를 동시에 받는 수. 공공데이터포털이 느려서 첫 채우기에 쓴다.
    """
    if store["through"]:
        start = _month_add(date.fromisoformat(store["through"] + "-01"), 1)
    else:
        start = _month_add(upto, -(BACKFILL_MONTHS - 1))
        store["since"] = start.strftime("%Y-%m")
    months = []
    m = start
    while m <= upto:
        months.append(m)
        m = _month_add(m, 1)
    if max_months is not None:
        months = months[:max_months]
    for m in months:
        ym = m.strftime("%Y%m")
        n = 0
        if workers > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(workers) as ex:
                got = dict(zip(codes, ex.map(lambda c: fetch(c, ym), codes)))
        else:
            got = {c: fetch(c, ym) for c in codes}
        for code in codes:
            for r in got[code]:
                if not usable(r):
                    continue
                k, v, d = kind_key(r, code), man(r), deal_date(r)
                cur = store["max"].get(k)
                if cur is None or v > cur[0]:
                    store["max"][k] = [v, d]
                n += 1
        store["through"] = m.strftime("%Y-%m")
        # 한 달 끝날 때마다 저장 — 중간에 끊겨도(9/22 첫 채우기가 23개월째에 끊겼다)
        # 다음 실행이 그다음 달부터 이어 간다.
        save(store)
        log(f"  신고가 기록 {store['through']} 채움 ({n:,}건)")
    return len(months)


def fill_back(store: dict, fetch, codes: dict[str, str], start: date, log=print,
              workers: int = 5) -> None:
    """이미 채운 기간보다 앞쪽(start ~ since 전 달)을 채운다. 최고가는 순서와 상관없이 합쳐진다.

    진행 위치는 store['back_done'] 에 남겨, 끊겨도 이어서 채운다.
    """
    first = date.fromisoformat(store["since"] + "-01")
    m = date.fromisoformat(store["back_done"] + "-01") if store.get("back_done") else first
    from concurrent.futures import ThreadPoolExecutor
    while True:
        m = _month_add(m, -1)
        if m < start:
            break
        ym = m.strftime("%Y%m")
        with ThreadPoolExecutor(workers) as ex:
            got = dict(zip(codes, ex.map(lambda c: fetch(c, ym), codes)))
        n = 0
        for code in codes:
            for r in got[code]:
                if not usable(r):
                    continue
                k, v, d = kind_key(r, code), man(r), deal_date(r)
                cur = store["max"].get(k)
                if cur is None or v > cur[0]:
                    store["max"][k] = [v, d]
                n += 1
        store["back_done"] = m.strftime("%Y-%m")
        save(store)
        log(f"  신고가 기록 {store['back_done']} 채움 ({n:,}건)")
    store["since"] = start.strftime("%Y-%m")
    store.pop("back_done", None)
    save(store)


def detect(store: dict, recent: list[tuple[str, dict]], since: date) -> list[dict]:
    """최근 두 달 거래 [(구코드, 거래)] 중 since 이후 계약된 신고가."""
    rows = sorted(((code, r) for code, r in recent if usable(r)), key=lambda x: deal_date(x[1]))
    running: dict[str, int] = {}
    highs = []
    for code, r in rows:
        k, v, d = kind_key(r, code), man(r), deal_date(r)
        hist = store["max"].get(k)
        before = max(hist[0] if hist else 0, running.get(k, 0))
        if hist and v > before and d >= since.isoformat():
            highs.append({"sgg": code, "dong": r.get("umdNm", ""), "apt": r["aptNm"],
                          "area": float(r["excluUseAr"]), "floor": r.get("floor", ""),
                          "man": v, "date": d, "prev_man": before,
                          "prev_date": hist[1] if before == hist[0] else None})
        running[k] = max(running.get(k, 0), v)
    return sorted(highs, key=lambda h: -(h["man"] - h["prev_man"]) / h["prev_man"])
