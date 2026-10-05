"""'잠깐! 경제 용어' 쇼츠 (10/4 동화님)."""
from brief.shorts import term as T


def test_terms_file_is_well_formed():
    terms = T.load()
    ids = [t["id"] for t in terms]
    assert len(ids) == len(set(ids)) >= 10
    for t in terms:
        assert t["term"] and t["hook"] and t["screen"] and t["speech"] and t["source_word"]


def test_next_term_in_order_and_stops_at_end():
    terms = T.load()
    assert T.next_term([])["id"] == terms[0]["id"]
    assert T.next_term([terms[0]["id"]])["id"] == terms[1]["id"]
    assert T.next_term([t["id"] for t in terms]) is None


def test_build_with_and_without_live_number():
    fx = next(t for t in T.load() if t["id"] == "fx")
    payload = {"dashboard": [{"id": "USDKRW", "close": "1,356.84", "change": "+0.47%"}], "kr_date": "2026-10-02"}
    sc = T.build(fx, payload, number=1)
    kinds = [s.kind for s in sc.segments]
    assert kinds == ["hook", "term", "live", "outro"]
    assert sc.segments[0].speech.startswith("잠깐! 환율에 대해 정확히 알고 계신가요?")
    assert "10월 2일 마감 기준 원 달러 환율은 1,356.84원이에요" in sc.segments[2].speech
    assert [s.kind for s in T.build(fx, {"dashboard": payload["dashboard"]}).segments][2] == "outro"   # 기준일 모르면 말하지 않는다
    assert [s.kind for s in T.build(fx, None).segments] == ["hook", "term", "outro"]


def test_live_placeholder_in_hook():
    idx = next(t for t in T.load() if t["id"] == "stock_index")
    sc = T.build(idx, {"dashboard": [{"id": "KOSPI", "close": "6,971.35"}], "kr_date": "2026-10-02"})
    assert sc.headline == "코스피 6,971.35, 이 숫자는 뭘 뜻할까?"
    assert "{live}" not in T.build(idx, None).headline


def test_meta_cites_source():
    fx = next(t for t in T.load() if t["id"] == "fx")
    title, desc = T.meta(fx, T.build(fx))
    assert "잠깐! 경제 용어 '환율'" in title
    assert "한국은행 경제통계시스템(ECOS) 통계용어사전 '환율'" in desc


def test_term_slots_mon_wed_fri_sun():
    import importlib.util
    from datetime import date
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("run_mod", Path(__file__).resolve().parent.parent / "run.py")
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    got = {d: run.term_slot(date(2026, 10, d), {}) for d in range(5, 12)}
    assert got[5] == "2026-10-05T19:00:00+09:00"            # 월
    assert got[6] is None and got[8] is None                 # 화·목
    assert got[10] == "2026-10-11T19:00:00+09:00"           # 토 → 일요일 것
    assert got[11] is None                                    # 일요일엔 실행이 없다
    assert run.term_slot(date(2026, 10, 6), {"term_days": list(range(7))}) == "2026-10-06T19:00:00+09:00"
