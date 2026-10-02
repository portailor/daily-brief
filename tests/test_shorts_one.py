"""'오늘의 숫자' 쇼츠 (10/2 동화님) — 그날 가장 센 숫자 하나."""
from types import SimpleNamespace

from brief.shorts.script import build_one


def _payload(rows=(), gainers=(), flow=None, macro=(), brief="2026-10-07", kr="2026-10-06", us="2026-10-06"):
    base = {i: ("+0.30%", "+0.40", "50%") for i in ("KOSPI", "KOSDAQ", "SPX", "NASDAQ", "USDKRW", "DXY")}
    base.update({i: v for i, v in rows})
    dash = [{"id": i, "change": c, "sigma": s, "p52": p} for i, (c, s, p) in base.items()]
    return {"brief_date": brief, "date_short": "10/7(수)", "kr_date": kr, "us_date": us, "dashboard": dash,
            "flow_stats": [flow] if flow else [], "macro_view": list(macro),
            "detail": {"kr": {"gainers": [{"name": n, "chg_pct": p} for n, p in gainers], "issues": []}}}


def test_big_sigma_row_wins_with_context_and_past():
    macro = [{"id": "DXY", "cond": "달러인덱스가 하루에 평소의 1.5배 넘게 오른 날",
              "stats": [{"target": "코스피", "n": 32, "up": 53, "base": {"up": 57}}]}]
    sc = build_one(_payload(rows=[("DXY", ("+0.64%", "+2.19", "100%"))], gainers=[("가나다", 20.0)], macro=macro))
    kinds = [s.kind for s in sc.segments]
    assert kinds == ["hook", "why", "macro", "outro"]
    hook = sc.segments[0]
    assert hook.rows == [("달러인덱스", "+0.64%")] and hook.surprise
    assert "급등, 평소의 2.2배 · 1년 중 최고 근처" in sc.headline
    assert "평소의 1.5배 넘게 오른 날이 32번" in sc.segments[2].speech
    assert sc.title == "오늘의 숫자"


def test_big_stock_beats_mid_sigma_and_limit_up_only_at_30():
    p = _payload(rows=[("USDKRW", ("+0.80%", "+1.70", "60%"))], gainers=[("가나다", 56.15)])
    sc = build_one(p)
    assert sc.segments[0].rows[0][0] == "가나다"
    assert "상한가" not in sc.segments[0].speech and "상한가" not in sc.headline   # 상장 첫날 +56% 는 상한가가 아니다
    sc = build_one(_payload(gainers=[("라마바", 29.98)]))
    assert "상한가" in sc.headline


def test_flow_hook_and_streak():
    f = SimpleNamespace(market="KOSPI", investor="외국인합계", eok=-12000.0, sigma=-2.4, streak=-3)
    sc = build_one(_payload(flow=f))
    assert sc.segments[0].rows[0][0] == "외국인 코스피"
    assert "3거래일 연속 순매도" in sc.segments[1].speech


def test_quiet_day_still_has_one_number():
    sc = build_one(_payload())
    assert sc is not None and sc.segments[0].kind == "hook"


def test_both_markets_closed_no_video():
    assert build_one(_payload(brief="2026-12-26", kr="2026-12-24", us="2026-12-24")) is None
