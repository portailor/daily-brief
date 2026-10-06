"""'오늘의 숫자' 쇼츠 — 10/6 동화님: 확률보다 오늘 오르고 내린 종목 중심, 확률은 평소와 많이 다를 때만."""
from types import SimpleNamespace

from brief.shorts.script import build_one


def _payload(rows=(), gainers=(), losers=(), macro=(), us_big=(), issues=(), brief="2026-10-07",
             kr="2026-10-06", us="2026-10-06"):
    base = {i: ("+0.30%", "+0.40", "50%") for i in ("KOSPI", "KOSDAQ", "SPX", "NASDAQ", "USDKRW", "DXY")}
    base.update({i: v for i, v in rows})
    dash = [{"id": i, "change": c, "sigma": s, "p52": p} for i, (c, s, p) in base.items()]
    return {"brief_date": brief, "date_short": "10/7(수)", "kr_date": kr, "us_date": us, "dashboard": dash,
            "flow_stats": [], "macro_view": list(macro),
            "detail": {"kr": {"gainers": [{"name": n, "chg_pct": p} for n, p in gainers],
                              "losers": [{"name": n, "chg_pct": p} for n, p in losers], "issues": list(issues)},
                       "us": {"big": [{"name": n, "chg_pct": p} for n, p in us_big]}}}


def test_stock_story_first_with_news_and_other_side():
    iss = [{"name": "가나다", "news": [{"title": "가나다, 신약 임상 3상 성공", "url": "u"}], "disclosures": []}]
    sc = build_one(_payload(gainers=[("가나다", 24.5)], losers=[("라마바", -12.1)], issues=iss))
    kinds = [s.kind for s in sc.segments]
    assert kinds == ["hook", "news", "other", "market", "outro"]
    assert sc.segments[0].rows == [("가나다", "+24.50%")] and sc.segments[0].surprise
    assert "가나다, 신약 임상 3상 성공" in sc.segments[1].speech
    assert "반대로 라마바는 12.1퍼센트 내렸어요" in sc.segments[2].speech
    assert sc.headline == "가나다 +24.5% 급등, 라마바 -12.1%"


def test_bigger_loser_leads_and_limit_down_word():
    sc = build_one(_payload(gainers=[("가", 8.0)], losers=[("나", -29.9)]))
    assert sc.segments[0].rows[0][0] == "나" and "하한가" in sc.headline


def test_no_limit_up_for_listing_day():
    sc = build_one(_payload(gainers=[("브릴스", 56.15)]))
    assert "상한가" not in sc.headline and "급등" in sc.headline


def test_probability_only_when_far_from_usual():
    macro = [{"id": "USDKRW", "cond": "원/달러 환율이 하루에 평소의 1.5배 넘게 내린 날",
              "stats": [{"target": "코스피", "n": 20, "up": 60, "base": {"up": 57}}]}]
    rows = [("USDKRW", ("-1.42%", "-2.20", "30%"))]
    sc = build_one(_payload(rows=rows, gainers=[("가", 12.0)], macro=macro))
    assert "macro" not in [s.kind for s in sc.segments]               # 60% vs 57% — 말하지 않는다
    mk = next(s for s in sc.segments if s.kind == "market")
    assert "평소 하루 움직임의 2.2배" in mk.speech                       # 큰 환율 움직임은 시장 한 줄에
    macro[0]["stats"][0]["up"] = 80
    sc = build_one(_payload(rows=rows, gainers=[("가", 12.0)], macro=macro))
    assert "macro" in [s.kind for s in sc.segments]                   # 80% vs 57% — 말한다


def test_kr_closed_uses_us_big_movers():
    sc = build_one(_payload(brief="2026-10-06", kr="2026-10-02", us="2026-10-05",
                            gainers=[("옛종목", 20.0)], us_big=[("엔비디아", 3.2), ("테슬라", -4.1)]))
    assert sc.segments[0].rows[0][0] == "테슬라"
    assert "미국 대형주 중" in sc.segments[0].speech
    assert "closed" in [s.kind for s in sc.segments]


def test_both_markets_closed_no_video():
    assert build_one(_payload(brief="2026-12-26", kr="2026-12-24", us="2026-12-24")) is None


def test_monday_is_last_week_story():
    from brief.interpret.weekly import WeekMove
    week = SimpleNamespace(period="9/28(월) ~ 10/2(금)", flows=[{"market": "KOSPI", "total_eok": -15000.0}],
                           moves=[WeekMove("KOSPI", "코스피", 6971.0, 2.71, "index", 2),
                                  WeekMove("SPX", "S&P 500", 7666.0, 1.21, "index", 2)])
    iss = [{"name": "HLB", "news": [{"title": "[특징주] HLB, 신약 허가 기대에 급등", "url": "u"}], "disclosures": []}]
    p = _payload(brief="2026-10-12", kr="2026-10-09", us="2026-10-09", gainers=[("오늘종목", 15.0)])
    p["week"] = week
    p["detail"]["kr_week"] = {"gainers": [{"name": "HLB", "chg_pct": 32.4}],
                              "losers": [{"name": "와이즈플래닛컴퍼니", "chg_pct": -61.3}], "issues": iss}
    sc = build_one(p, weekly=True)
    assert sc.title == "지난주의 숫자"
    assert sc.segments[0].rows[0][0] == "와이즈플래닛컴퍼니"                # 더 크게 움직인 쪽이 먼저
    assert "일주일 만에 61.3퍼센트나 내렸어요" in sc.segments[0].speech
    other = next(s for s in sc.segments if s.kind == "other")
    assert "HLB는 한 주 동안 32.4퍼센트 올랐어요" in other.speech
    mk = next(s for s in sc.segments if s.kind == "market")
    assert "외국인은 코스피에서 한 주 동안" in mk.speech and "순매도" in mk.speech
    assert "오늘종목" not in " ".join(s.speech for s in sc.segments)          # 금요일 하루 숫자를 되풀이하지 않는다
    assert sc.headline.startswith("지난주 와이즈플래닛컴퍼니 -61.3% 급락")


def test_monday_without_week_data_falls_back():
    p = _payload(gainers=[("가", 12.0)])
    assert build_one(p, weekly=True).title == "오늘의 숫자"
