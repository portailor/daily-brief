"""'오늘의 숫자' 쇼츠 — 10/6 동화님: 확률보다 오늘 오르고 내린 종목 중심, 확률은 평소와 많이 다를 때만.
10/9 동화님: 낯선 소형주보다 누구나 아는 것(환율·코스피 큰 움직임, 시총 2조원↑ 종목)부터."""
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
    assert sc.segments[0].react == "cheer"                              # 크게 오르면 손 번쩍
    assert "가나다, 신약 임상 3상 성공" in sc.segments[1].speech
    assert "반대로 라마바는 12.1퍼센트 내렸어요" in sc.segments[2].speech
    assert sc.headline == "하루 +24.5% 급등, 무슨 일이? (가나다)"          # 낯선 이름은 숫자부터


def test_bigger_loser_leads_and_limit_down_word():
    sc = build_one(_payload(gainers=[("가", 8.0)], losers=[("나", -29.9)]))
    assert sc.segments[0].rows[0][0] == "나" and "하한가" in sc.headline
    assert sc.segments[0].react == "cry"
    assert "무슨 일이" not in sc.headline                                # 읽어 줄 기사가 없으면 묻지 않는다


def test_no_limit_up_for_listing_day():
    sc = build_one(_payload(gainers=[("브릴스", 56.15)]))
    assert "상한가" not in sc.headline and "급등" in sc.headline


def test_probability_only_when_far_from_usual():
    macro = [{"id": "USDKRW", "cond": "원/달러 환율이 하루에 평소의 1.5배 넘게 내린 날",
              "stats": [{"target": "코스피", "n": 20, "up": 60, "base": {"up": 57}}]}]
    rows = [("USDKRW", ("-1.42%", "-2.20", "30%"))]
    sc = build_one(_payload(rows=rows, gainers=[("가", 24.0)], macro=macro))
    assert sc.segments[0].rows[0][0] == "원/달러 환율"                  # 환율이 평소의 2배 넘게 — 환율이 주인공
    assert "macro" not in [s.kind for s in sc.segments]               # 60% vs 57% — 말하지 않는다
    macro[0]["stats"][0]["up"] = 80
    sc = build_one(_payload(rows=rows, gainers=[("가", 24.0)], macro=macro))
    assert "macro" in [s.kind for s in sc.segments]                   # 80% vs 57% — 말한다


def test_big_fx_line_when_stock_leads():
    rows = [("USDKRW", ("-0.92%", "-1.70", "30%")), ("KOSPI", ("+0.30%", "+2.10", "50%"))]
    p = _payload(rows=rows, gainers=[("가", 24.0)])
    sc = build_one(p)                                                 # 코스피 σ2.1 → 코스피가 주인공
    assert sc.segments[0].rows[0][0] == "코스피"
    rows[1] = ("KOSPI", ("+0.30%", "+0.40", "50%"))
    rows[0] = ("USDKRW", ("-0.92%", "-1.90", "30%"))
    sc = build_one(_payload(rows=rows, gainers=[("가", 24.0)]))        # 1.9배 — 종목이 주인공, 환율은 지표 2배 미만이라 시장 한 줄에 없음
    assert sc.segments[0].rows[0][0] == "가"


def test_known_stock_beats_unknown_limit_up():
    p = _payload(gainers=[("샌즈랩", 30.0)], losers=[("브릴스", -10.6)])
    p["detail"]["kr"]["kospi_top"] = [{"name": "삼성전자", "chg_pct": -3.4, "cap_jo": 512.0}]
    p["detail"]["kr_known"] = {"gainers": [{"name": "에코프로", "chg_pct": 2.0, "cap_jo": 9.0}],
                               "losers": [{"name": "삼성전자", "chg_pct": -3.4, "cap_jo": 512.0}],
                               "issues": [{"name": "삼성전자", "news": [{"title": "삼성전자, 외국인 매도에 3%대 하락", "url": "u"}],
                                           "disclosures": []}]}
    sc = build_one(p)
    hook = sc.segments[0]
    assert hook.rows[0][0] == "삼성전자"                                # 시총 상위 10개는 3%만 움직여도
    assert "시가총액 약 512조 원 규모의 삼성전자가" in hook.speech
    assert hook.note == "대형주 중 가장 많이 내린 종목"
    assert "삼성전자, 외국인 매도에 3%대 하락" in sc.segments[1].speech
    assert sc.headline == "삼성전자 하루 -3.4% 하락, 무슨 일이?"            # 아는 이름은 이름부터
    other = next(s for s in sc.segments if s.kind == "other")
    assert other.rows[0][0] == "샌즈랩"                                  # 반대쪽 이름 있는 종목이 없으면 오늘 가장 많이 오른 종목


def test_small_cap_needs_20_percent():
    p = _payload(gainers=[("가", 15.0)], rows=[("USDKRW", ("-0.92%", "-1.60", "30%"))])
    sc = build_one(p)                                                 # 소형주 +15% < 20% → 평소의 1.6배 환율
    assert sc.segments[0].rows[0][0] == "원/달러 환율"
    sc = build_one(_payload(gainers=[("가", 15.0)]))                  # 다른 게 없으면 그래도 종목
    assert sc.segments[0].rows[0][0] == "가"


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
    assert sc.headline == "지난주 -61.3% 급락 (와이즈플래닛컴퍼니)"


def test_monday_without_week_data_falls_back():
    p = _payload(gainers=[("가", 12.0)])
    assert build_one(p, weekly=True).title == "오늘의 숫자"


def test_bigger_name_first_unless_small_one_moved_twice_as_much():
    def run(known_g, known_l):
        p = _payload(gainers=[("소형", 12.0)])
        p["detail"]["kr_known"] = {"gainers": known_g, "losers": known_l}
        return build_one(p).segments[0].rows[0][0]
    # 10/7 실제 — 케이씨텍(2.4조) +14.5% 보다 주성엔지니어링(11.5조) -11.1%
    assert run([{"name": "케이씨텍", "chg_pct": 14.5, "cap_jo": 2.4}],
               [{"name": "주성엔지니어링", "chg_pct": -11.1, "cap_jo": 11.5},
                {"name": "LG전자", "chg_pct": -10.5, "cap_jo": 33.9}]) == "주성엔지니어링"
    # 10/8 실제 — 펩트론(2.1조) 하한가는 주성엔지니어링 +8.1% 의 두 배 넘게
    assert run([{"name": "주성엔지니어링", "chg_pct": 8.1, "cap_jo": 12.4}],
               [{"name": "펩트론", "chg_pct": -29.9, "cap_jo": 2.1}]) == "펩트론"
