"""휴장일 쇼츠 — 한국장만 쉰 날, 미국장만 쉰 날, 둘 다 쉰 날, 쉬기 전 날 안내 (9/27 동화님)."""
from types import SimpleNamespace

from brief import market_calendar as mc
from brief.shorts.script import build, market_status


def _payload(brief, kr, us):
    dash = [{"id": i, "change": c, "sigma": "+0.20", "p52": "50%"} for i, c in
            (("KOSPI", "+0.50%"), ("KOSDAQ", "-0.20%"), ("SPX", "-0.30%"), ("NASDAQ", "+0.10%"),
             ("DOW", "+0.05%"), ("USDKRW", "-0.10%"), ("US10Y", "+2bp"), ("WTI", "+1.00%"), ("GOLD", "-0.40%"))]
    trig = [SimpleNamespace(instrument="KTB3Y", name="국고채 3년물", kind="band_edge", threshold=0,
                            probability=0.3, situation="", prob_name="내려올 확률", is_uncertain=False),
            SimpleNamespace(instrument="SPX", name="S&P 500", kind="band_edge", threshold=0,
                            probability=0.4, situation="", prob_name="내려올 확률", is_uncertain=False)]
    return {"brief_date": brief, "date_short": brief[5:], "kr_date": kr, "us_date": us,
            "dashboard": dash, "verdict": {"headline": "코스피가 +0.50%로 평소 변동폭의 2.1배만큼 크게 올랐습니다."},
            "triggers": trig, "detail": {"kr": {"sectors": [{"name": "건설", "chg_pct": 1.0},
                                                            {"name": "화학", "chg_pct": -1.0}]}}}


def test_calendar_names():
    from datetime import date
    assert mc.holiday_name("KR", date(2026, 9, 24)) == "추석 연휴"
    assert mc.holiday_name("US", date(2026, 11, 26)) == "추수감사절"
    assert mc.holiday_name("KR", date(2026, 9, 23)) is None
    assert mc.holiday_name("US", date(2026, 9, 26)) is None           # 주말은 휴장일로 치지 않는다


def test_kr_closed_next_day_us_only():
    sc = build(_payload("2026-09-25", "2026-09-23", "2026-09-24"))
    kinds = [s.kind for s in sc.segments]
    assert "closed" in kinds and "us" in kinds
    assert not {"kr", "sectors", "verdict"} & set(kinds)             # 한국 부분·한국 결론 없음
    closed = next(s for s in sc.segments if s.kind == "closed")
    assert "추석 연휴로 한국 증시가 쉬었어요" in closed.speech
    trig = next(s for s in sc.segments if s.kind == "triggers")
    assert "국고채" not in trig.speech
    assert sc.mood == "down"                                          # S&P 500 방향


def test_us_closed_next_day_kr_only():
    sc = build(_payload("2026-11-27", "2026-11-26", "2026-11-25"))
    kinds = [s.kind for s in sc.segments]
    assert "kr" in kinds and "us" not in kinds
    closed = next(s for s in sc.segments if s.kind == "closed")
    assert "추수감사절로 미국 증시가 쉬었어요" in closed.speech
    fx = next(s for s in sc.segments if s.kind == "fx")
    assert all("10년물" not in a for a, _ in fx.rows)


def test_both_closed_no_video():
    assert build(_payload("2026-12-26", "2026-12-24", "2026-12-24")) is None


def test_ahead_notice_day_before():
    sc = build(_payload("2026-09-23", "2026-09-22", "2026-09-22"))
    ahead = next(s for s in sc.segments if s.kind == "ahead")
    assert "내일부터 9월 25일까지 추석 연휴로 한국 증시는 쉬어요" in ahead.speech
    assert sc.segments[-2].kind == "ahead"                            # 마무리 바로 앞


def test_ahead_both_today_skips_tomorrow():
    sc = build(_payload("2026-12-25", "2026-12-24", "2026-12-24"))
    ahead = next(s for s in sc.segments if s.kind == "ahead")
    assert "오늘은 성탄절로 한국과 미국 증시가 모두 쉬어요" in ahead.speech
    assert "내일은 영상을 쉬어 갈게요" in ahead.speech


def test_normal_day_unchanged():
    st = market_status(_payload("2026-09-22", "2026-09-21", "2026-09-21"))
    assert st["kr_fresh"] and st["us_fresh"] and not st["ahead"]


def test_official_names_mapping():
    assert mc._kr_name("대체공휴일(개천절)") == "개천절 대체공휴일"
    assert mc._kr_name("기독탄신일") == "성탄절"
    assert mc._kr_name("전국동시지방선거") == "지방선거"
    assert mc._kr_name("제헌절") == "제헌절"
