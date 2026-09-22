"""서울 신고가 판정 — 틀리면 '신고가'라는 없는 뉴스가 나간다. 네트워크 안 씀."""
from datetime import date

from brief.collect import records


def _r(apt, area, amount, y, m, d, gbn="중개거래", cancel=""):
    return {"aptNm": apt, "umdNm": "역삼동", "jibun": f"{apt}-1", "excluUseAr": str(area),
            "dealAmount": f"{amount:,}",
            "dealYear": str(y), "dealMonth": str(m), "dealDay": str(d),
            "dealingGbn": gbn, "cdealType": cancel, "floor": "10"}


def _store(**maxes):
    return {"since": "2023-08", "through": "2026-07",
            "max": {f"11680|역삼동|{k}-1|84.9": v for k, v in maxes.items()}}


def test_new_high_over_history():
    st = _store(A=[300000, "2025-11-02"])
    got = records.detect(st, [("11680", _r("A", 84.9, 320000, 2026, 9, 18))], date(2026, 9, 15))
    assert len(got) == 1 and got[0]["prev_man"] == 300000 and got[0]["prev_date"] == "2025-11-02"


def test_equal_price_is_not_a_record():
    st = _store(A=[300000, "2025-11-02"])
    assert records.detect(st, [("11680", _r("A", 84.9, 300000, 2026, 9, 18))], date(2026, 9, 15)) == []


def test_unknown_kind_is_not_a_record():
    # 기록에 없는 단지·면적(첫 거래)은 신고가라고 하지 않는다
    assert records.detect(_store(), [("11680", _r("B", 84.9, 999999, 2026, 9, 18))], date(2026, 9, 15)) == []


def test_cancelled_and_direct_deals_ignored():
    st = _store(A=[300000, "2025-11-02"])
    rows = [("11680", _r("A", 84.9, 350000, 2026, 9, 18, cancel="O")),
            ("11680", _r("A", 84.9, 360000, 2026, 9, 19, gbn="직거래"))]
    assert records.detect(st, rows, date(2026, 9, 15)) == []


def test_must_beat_earlier_recent_deal():
    # 지난달에 이미 330,000 에 팔렸으면 이번 주 320,000 은 신고가가 아니다
    st = _store(A=[300000, "2025-11-02"])
    rows = [("11680", _r("A", 84.9, 330000, 2026, 8, 20)),
            ("11680", _r("A", 84.9, 320000, 2026, 9, 18))]
    assert records.detect(st, rows, date(2026, 9, 15)) == []


def test_nearby_area_counts_as_same_type():
    # 같은 단지 84.9㎡ 최고가 30억 — 84.2㎡ 가 30억에 팔린 건 신고가가 아니다 (±1㎡ 한 평형)
    st = _store(A=[300000, "2025-11-02"])
    assert records.detect(st, [("11680", _r("A", 84.2, 300000, 2026, 9, 18))], date(2026, 9, 15)) == []


def test_renamed_complex_matched_by_jibun():
    # 단지 이름이 바뀌어도 지번이 같으면 같은 단지로 본다
    st = _store(A=[300000, "2021-10-02"])
    row = _r("A", 84.9, 290000, 2026, 9, 18)
    row["aptNm"] = "A 리모델링 새 이름"
    assert records.detect(st, [("11680", row)], date(2026, 9, 15)) == []
