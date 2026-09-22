"""브리핑 HTML 리포트 생성.

여기서도 문장을 '지어내지' 않는다. 계산된 값을 정해진 틀에 끼워 넣을 뿐이다.
용어 설명은 config/terms.json 에서만 나온다.
"""
from __future__ import annotations

from dataclasses import asdict

import sys
from datetime import datetime
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from brief import db                                            # noqa: E402
from brief.collect.market import Instrument, load_instruments   # noqa: E402
from brief.collect import flows as flows_mod                    # noqa: E402
from brief import clock                                         # noqa: E402
from brief.interpret import rules, weekly as weekly_mod          # noqa: E402
from brief.collect import events as events_mod                  # noqa: E402
from brief.collect import detail as detail_mod                  # noqa: E402
from brief.collect import results as results_mod                # noqa: E402
from brief.collect import realestate as realestate_mod          # noqa: E402
from brief.collect import crypto as crypto_mod                  # noqa: E402
from brief.render.terms import Glossary                         # noqa: E402
from brief.score import scorer                                  # noqa: E402

TPL_DIR = Path(__file__).resolve().parent / "templates"
OUT_DIR = ROOT / "docs"

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

# 표에 세로로 세울 순서. 국내 → 미국 → 금리 → 원자재 순으로
# 한국 투자자가 아침에 훑는 동선에 맞춘다.
GROUP_ORDER = ["kr_equity", "kr_rates", "fx", "us_equity",
               "sentiment", "rates", "commodity"]


def _fmt_num(v: float | None, dec: int) -> str:
    return "—" if v is None else f"{v:,.{dec}f}"


def build_dashboard(rows, newest: str, insts: dict[str, Instrument],
                    cfg: dict, gloss: Glossary, seen: set[str]) -> list[dict]:
    out = []
    for r in rows:
        inst = insts.get(r["instrument"])
        if not inst:
            continue

        if inst.kind == "rate":
            chg = r["chg_bp"]
            change = "—" if chg is None else f"{chg:+,.0f}bp"
        else:
            chg = r["chg_pct"]
            change = "—" if chg is None else f"{chg:+.2f}%"

        direction = "flat" if not chg else ("up" if chg > 0 else "down")
        sigma = r["sigma"] if inst.predict else None
        notable = (inst.can_claim and sigma is not None
                   and abs(sigma) >= cfg["sigma_notable"])

        vs200 = r["vs_ma200"]
        # 미국 지표는 한국보다 하루 늦게 마감하므로 기준일이 다르다.
        # 다른 날짜의 값이 한 표에 섞여 있다는 사실을 숨기지 않는다.
        asof = "" if r["trade_date"] == newest else \
            clock.label(r["trade_date"]).split("(")[0]

        out.append({
            "id": inst.id,
            "group": inst.group,
            "asof": asof,
            "futures": inst.futures,
            "name_html": gloss.annotate(inst.name, seen),
            "close": _fmt_num(r["close"], inst.decimals),
            "change": change,
            "dir": direction,
            "sigma": "—" if sigma is None else f"{sigma:+.2f}",
            "sigma_cls": "up" if notable and (sigma or 0) > 0 else (
                         "down" if notable else "flat"),
            "p52": "—" if r["pct_52w"] is None else f"{r['pct_52w']:.0f}%",
            "band_pos": 0 if r["pct_52w"] is None else round(min(97, max(0, r["pct_52w"]))),
            "vs_ma200": "—" if vs200 is None else f"{vs200:+.1f}%",
            "ma_dir": "flat" if vs200 is None else ("up" if vs200 > 0 else "down"),
            "notable": notable,
        })

    # 그룹 순서 → 그룹 안에서는 settings.yaml 에 적힌 순서 (코스피가 코스닥보다 위)
    order = list(insts)
    out.sort(key=lambda x: (GROUP_ORDER.index(x["group"]) if x["group"] in GROUP_ORDER else 99,
                            order.index(x["id"])))
    return out


def build_verdict(rows, insts: dict[str, Instrument], cfg: dict) -> dict:
    """오늘의 한 줄 결론. '특별한 게 없다'도 정당한 결론으로 취급한다."""
    nb = sorted([r for r in rows if r["sigma"] is not None
                 and r["instrument"] in insts and insts[r["instrument"]].can_claim],
                key=lambda r: -abs(r["sigma"]))
    hot = [r for r in nb if abs(r["sigma"]) >= cfg["sigma_notable"]]

    if not hot:
        top = nb[0] if nb else None
        sub = ""
        if top is not None:
            name = insts[top["instrument"]].name
            sub = (f"가장 크게 움직인 건 {name}(σ {top['sigma']:+.2f})이지만 "
                   f"이것도 평소 변동폭 안입니다.")
        return {
            "headline": "오늘은 평소와 다른 움직임이 없습니다. 특별히 할 일이 없는 날입니다.",
            "sub": sub,
        }

    names = ", ".join(insts[r["instrument"]].name for r in hot[:3])
    lead = hot[0]
    lead_name = insts[lead["instrument"]].name
    word = "크게 올랐습니다" if lead["sigma"] > 0 else "크게 내렸습니다"
    move = (f"{lead['chg_bp']:+.0f}bp" if insts[lead["instrument"]].kind == "rate"
            else f"{lead['chg_pct']:+.2f}%")

    return {
        # '평소의 N배' 는 σ(직전 60거래일 변동의 표준편차 대비 배수)를 말한다
        "headline": f"{lead_name}{rules.josa(lead_name)} {move}로 "
                    f"평소 변동폭의 {abs(lead['sigma']):.1f}배만큼 {word}.",
        "sub": f"오늘 평소 범위를 벗어난 지표: {names}. 나머지는 평범한 하루였습니다.",
    }


def build_notable_lines(rows, insts: dict[str, Instrument],
                        cfg: dict, gloss: Glossary, seen: set[str]) -> list[str]:
    """σ가 큰 지표와, 52주 밴드 극단에 있는 지표를 문장으로."""
    lines: list[str] = []

    for r in sorted(rows, key=lambda x: -abs(x["sigma"] or 0)):
        inst = insts.get(r["instrument"])
        if not inst or not inst.can_claim:
            continue
        s, p52 = r["sigma"], r["pct_52w"]

        if s is not None and abs(s) >= cfg["sigma_notable"]:
            move = (f"{r['chg_bp']:+.0f}bp" if inst.kind == "rate"
                    else f"{r['chg_pct']:+.2f}%")
            lines.append(gloss.annotate(
                f"{inst.name} {move} — 평소 하루 변동폭의 {abs(s):.1f}배입니다. "
                f"σ 기준 {'드문' if abs(s) >= cfg['sigma_extreme'] else '눈여겨볼'} 움직임입니다.",
                seen))

        if p52 is not None and p52 <= 5:
            lines.append(gloss.annotate(
                f"{inst.name}{rules.josa(inst.name)} 52주 최저 부근입니다 "
                f"(밴드 {p52:.0f}%, 1년 최저 {_fmt_num(r['low_52w'], inst.decimals)}). "
                f"지난 1년 중 가장 낮은 수준이라는 뜻입니다.", seen))
        elif p52 is not None and p52 >= 95:
            lines.append(gloss.annotate(
                f"{inst.name}{rules.josa(inst.name)} 52주 최고 부근입니다 "
                f"(밴드 {p52:.0f}%, 1년 최고 {_fmt_num(r['high_52w'], inst.decimals)}). "
                f"지난 1년 중 가장 높은 수준이라는 뜻입니다.", seen))

    return lines[:6]


def _eok(v: float) -> str:
    """억원을 사람이 읽는 단위로. 1조 넘으면 조 단위로 접는다."""
    a = abs(v)
    s = f"{a / 10_000:.2f}조" if a >= 10_000 else f"{a:,.0f}억"
    return ("+" if v > 0 else "−" if v < 0 else "") + s


def build_flows(conn, gloss: Glossary, seen: set[str]) -> tuple[list[dict], list[str]]:
    rows, lines = [], []

    for market in flows_mod.MARKETS:
        for inv in flows_mod.INVESTORS:
            st = flows_mod.analyze(conn, market, inv)
            if st is None:
                continue

            notable = (st.sigma is not None and abs(st.sigma) >= 1.5) or abs(st.streak) >= 3
            rows.append({
                "label": f"{market} {inv.replace('합계', '')}",
                "amount": _eok(st.eok),
                "dir": "up" if st.net_buy > 0 else "down",
                "streak": f"{abs(st.streak)}일" if abs(st.streak) >= 2 else "—",
                "streak_cls": ("up" if st.streak > 0 else "down") if abs(st.streak) >= 2 else "flat",
                "sigma": "—" if st.sigma is None else f"{st.sigma:+.2f}",
                "sigma_cls": ("up" if (st.sigma or 0) > 0 else "down") if notable else "flat",
                "ma5": _eok(st.ma5),
                "ma5_dir": "up" if st.ma5 > 0 else "down",
                "notable": notable,
            })

            # 외국인은 연속 2일 이상이면 문장으로도 짚는다
            if inv == "외국인합계" and abs(st.streak) >= 2:
                lines.append(gloss.annotate(flows_mod.describe(st), seen))

    return rows, lines


TILE_IDS = [("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("SPX", "S&P 500"),
            ("NASDAQ", "나스닥"), ("USDKRW", "원/달러"), ("US10Y", "미 국채 10년")]


def build_tiles(rows, insts: dict[str, Instrument]) -> list[dict]:
    """머리말의 핵심 숫자. 해석 없이 종가와 변화만."""
    by_id = {r["instrument"]: r for r in rows}
    out = []
    for iid, short in TILE_IDS:
        r, inst = by_id.get(iid), insts.get(iid)
        if not r or not inst:
            continue
        if inst.kind == "rate":
            chg, txt = r["chg_bp"], ("—" if r["chg_bp"] is None else f"{r['chg_bp']:+.0f}bp")
        else:
            chg, txt = r["chg_pct"], ("—" if r["chg_pct"] is None else f"{r['chg_pct']:+.2f}%")
        out.append({"id": iid, "name": short, "close": _fmt_num(r["close"], inst.decimals),
                    "change": txt, "chg": chg or 0,
                    "dir": "flat" if not chg else ("up" if chg > 0 else "down"),
                    "date": clock.label(r["trade_date"])})
    return out


def _eok_text(v: float) -> str:
    a = abs(v)
    return f"{a / 10_000:.2f}조원" if a >= 10_000 else f"{a:,.0f}억원"


CASES_PATH = ROOT / "config" / "cases.json"
CASE_EPOCH = "2026-09-15"


def pick_case(today) -> tuple[dict | None, int, int]:
    """사례집에서 오늘 차례의 사례. 기준일부터 하루에 하나씩 순서대로 돈다."""
    import json
    from datetime import date
    try:
        cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return None, 0, 0
    if not cases:
        return None, 0, 0
    i = (today - date.fromisoformat(CASE_EPOCH)).days % len(cases)
    return cases[i], i + 1, len(cases)


def render(trade_date: str | None = None,
           save_predictions: bool = True,
           mode: str = "daily") -> tuple[Path, dict]:
    """mode='daily' 는 일일 브리핑, 'weekly' 는 월요일 주간 정리."""
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    acfg = cfg["analysis"]
    insts = {i.id: i for i in load_instruments()}
    gloss = Glossary()
    seen: set[str] = set()

    card = scorer.score_pending()

    with db.session() as conn:
        snapshot = db.latest_snapshot(conn)
        if not snapshot:
            raise SystemExit("데이터가 없습니다. 먼저 수집과 분석을 실행하세요.")

        # 표의 기준일은 '가장 많은 지표가 공유하는 날짜'로 잡는다.
        # 최신 날짜 하나를 기준으로 하면, 한 계열만 먼저 들어와도
        # 나머지 전부에 다른 날짜 배지가 붙어 오히려 읽기 어렵다.
        from collections import Counter
        newest = Counter(r["trade_date"] for r in snapshot).most_common(1)[0][0]

        by_id = {r["instrument"]: r["trade_date"] for r in snapshot}
        kr_date = by_id.get("KOSPI", newest)
        us_date = by_id.get("SPX", newest)
        trade_date = trade_date or max(kr_date, us_date)

        seen_table: set[str] = set()
        dashboard = build_dashboard(snapshot, newest, insts, acfg, gloss, seen_table)
        verdict = build_verdict(snapshot, insts, acfg)
        tiles = build_tiles(snapshot, insts)
        detail = detail_mod.load()
        notable = build_notable_lines(snapshot, insts, acfg, gloss, seen)
        trigs = rules.build_triggers(conn, snapshot, insts, acfg)[:6]

        flow_rows, flow_lines = build_flows(conn, gloss, seen)
        flow_stats = [st for m in flows_mod.MARKETS for inv in flows_mod.INVESTORS
                      if (st := flows_mod.analyze(conn, m, inv)) is not None]

        pockets = []
        for r in snapshot:
            txt = rules.pocket_translate(r["instrument"], r["close"], r["chg"],
                                         r["chg_pct"], r["chg_bp"])
            if txt:
                name = insts[r["instrument"]].name
                pockets.append(gloss.annotate(f"{name}: {txt}", seen))

        # 오늘 내놓은 조건을 예측으로 저장 → 내일 자동 채점된다.
        # made_on 은 그 지표의 실제 기준일이어야 채점 시점이 어긋나지 않는다.
        if save_predictions:
            asof = {r["instrument"]: r["trade_date"] for r in snapshot}
            for t in trigs:
                scorer.record(conn, asof[t.instrument], t.claim, t.instrument,
                              t.field, t.op, t.threshold, t.horizon, t.probability)

        track = scorer.track_record(conn)

        week = (weekly_mod.summarize(conn, clock.today_kst(), insts)
                if mode == "weekly" else None)


    # 일정: 주간 정리는 이번 주 전체, 일일 브리핑은 앞으로 일주일
    from datetime import timedelta
    today = clock.today_kst()
    if mode == "weekly":
        ev_start, ev_end = today, today + timedelta(days=6 - today.weekday())
        calendar_title = "이번 주 일정"
    else:
        ev_start, ev_end = today, today + timedelta(days=6)
        calendar_title = "다가오는 일정"
    try:
        upcoming, calendar_missing = events_mod.upcoming(ev_start, ev_end)
    except Exception:                                          # noqa: BLE001
        upcoming, calendar_missing = [], ["일정 전체"]

    # 지난 일정의 결과 — 실적·지표·금리 결정. 실패해도 리포트는 만든다.
    try:
        past_results, results_missing = results_mod.recent(today)
    except Exception:                                          # noqa: BLE001
        past_results, results_missing = [], ["지난 일정 결과"]

    # 부동산 탭 — 수집 단계에서 만든 data/realestate.json 을 읽는다.
    # 탭이 따로라 말풍선 중복 방지(seen)도 따로 둔다. 같은 용어라도 탭마다 한 번씩 뜬다.
    realestate = realestate_mod.load()
    seen_re: set = set()
    re_terms = {"weekly": gloss.annotate("주간 아파트 가격동향", seen_re),
                "jeonse": gloss.annotate("전세", seen_re)}
    for sec in ("kr_monthly", "us"):
        for it in realestate.get(sec, []) or []:
            it["name_html"] = gloss.annotate(it["name"], seen_re)

    # 코인 탭 — 말풍선 중복 방지(seen)는 탭마다 따로.
    crypto = crypto_mod.load()
    seen_cx: set = set()
    cx_terms = {k: gloss.annotate(v, seen_cx) for k, v in
                (("premium", "김치 프리미엄"), ("dominance", "비트코인 도미넌스"),
                 ("fng", "공포·탐욕 지수"), ("stable", "스테이블코인"))}

    trig_view = []
    for t in trigs:
        item = {"claim_html": gloss.annotate(t.claim, seen),
                "situation": gloss.annotate(t.situation, seen) if t.situation else "",
                "prob_name": t.prob_name,
                "basis": t.prob_text(), "probability": t.probability}
        if t.probability is not None and t.samples:
            hits = round(t.probability * t.samples)
            lo, hi = rules.wilson(hits, t.samples)
            # 표본이 적으면 막대의 진한 부분(점추정)을 그리지 않고 범위만 보여준다
            item |= {"pct": 0 if t.is_uncertain else round(t.probability * 100),
                     "label": t.prob_label(),
                     "ci_lo": round(lo * 100), "ci_w": round((hi - lo) * 100)}
        trig_view.append(item)

    brief_date = clock.today_kst()
    case, case_no, case_total = pick_case(brief_date)
    # 이 페이지만의 표식. 발송 전에 '웹에 올라간 게 정말 방금 만든 것인지'를
    # 확인하는 데 쓴다. 같은 날짜 페이지가 이미 있으면 200 응답만으로는 구분이 안 된다.
    build_id = datetime.now().strftime("%Y%m%d%H%M%S")
    basis = f"한국 {clock.label(kr_date)} · 미국 {clock.label(us_date)} 마감 기준"
    page_name = f"{brief_date.isoformat()}.html"

    env = Environment(loader=FileSystemLoader(TPL_DIR),
                      autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.globals["eok"] = _eok_text

    html = env.get_template("brief.html.j2").render(
        date_kr=clock.label_long(brief_date),
        is_weekly=(mode == "weekly"),
        tiles=tiles,
        detail=detail,
        realestate=realestate,
        re_terms=re_terms,
        crypto=crypto,
        cx_terms=cx_terms,
        case=case, case_no=case_no, case_total=case_total,
        kr_label=clock.label(detail["kr"]["date"]) if detail.get("kr", {}).get("date") else "",
        us_label=clock.label(detail["us"]["date"]) if detail.get("us", {}).get("date") else "",
        week=week,
        calendar=[{"when": e.when(), "title": e.title, "note": e.note,
                   "region": e.region, "importance": e.importance} for e in upcoming],
        calendar_title=calendar_title,
        calendar_missing=calendar_missing,
        results=past_results,
        results_missing=results_missing,
        basis=basis,
        trade_date=trade_date,
        verdict=verdict,
        score={"hit": card.hit, "miss": card.miss,
               "total_today": card.hit + card.miss,
               "total": card.total, "resolved": card.resolved},
        track=track,
        dashboard=dashboard,
        notable=notable,
        flows=flow_rows,
        flow_lines=flow_lines,
        pockets=pockets,
        triggers=trig_view,
        sources="한국거래소·yfinance(시세), 미 연준 FRED(거시지표·미국 주택), 한국은행 ECOS(국내금리·주택담보대출·미분양), 한국부동산원(아파트 가격), CoinGecko·업비트(코인)",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        build_id=build_id,
        stale="",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    # 날짜별 주소로도 남긴다. 카톡 링크는 이쪽을 가리킨다 —
    # index.html 은 매일 같은 주소라 GitHub Pages 캐시(약 10분)에 걸리면
    # 어제 내용이 그대로 보일 수 있다.
    (OUT_DIR / page_name).write_text(html, encoding="utf-8")

    notable_rows = [
        {"name": insts[r["instrument"]].name,
         "sigma": r["sigma"],
         "change": (f"{r['chg_bp']:+.0f}bp" if insts[r["instrument"]].kind == "rate"
                    else f"{r['chg_pct']:+.2f}%")}
        for r in sorted(snapshot, key=lambda x: -abs(x["sigma"] or 0))
        if r["sigma"] is not None and abs(r["sigma"]) >= acfg["sigma_notable"]
        and r["instrument"] in insts and insts[r["instrument"]].can_claim
    ]

    payload = {
        "verdict": verdict, "score": card, "track": track,
        "notable_rows": notable_rows, "flow_stats": flow_stats,
        "triggers": trigs, "trade_date": trade_date,
        "brief_date": brief_date.isoformat(),
        "date_kr": clock.label_long(brief_date),
        "date_short": clock.label(brief_date),
        "basis": basis,
        "page_name": page_name,
        "build_id": build_id,
        "mode": mode,
        "week": week,
        "tiles": tiles,
        "dashboard": dashboard,   # 메일 본문 표에 쓴다
        "detail": detail,
        "events": upcoming,
        "results": [asdict(r) for r in past_results],
        # 새 거래일 데이터가 들어왔는지 판단하는 열쇠 — 한국·미국 대표 지수의 기준일
        "market_key": f"KOSPI:{kr_date}|SPX:{us_date}",
        "kr_date": kr_date,
        "us_date": us_date,
    }
    return out, payload


if __name__ == "__main__":
    path, _ = render()
    size = path.stat().st_size
    print(f"생성 완료: {path}  ({size:,} bytes)")
