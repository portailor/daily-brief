"""지난 일정 결과 — 틀리면 안 되는 부분만 본다. 네트워크는 쓰지 않는다."""
from datetime import date

from brief.collect import results


def test_usd_units():
    assert results._usd(96_745_402_720) == "967.5억 달러"
    assert results._usd(6_760_000_000) == "67.6억 달러"
    assert results._usd(1_250_000_000_000) == "1.25조 달러"


def test_decision_words():
    assert results._decision(3.75, 3.75) == "동결"
    assert results._decision(3.75, 4.00) == "0.25%p 인상"
    assert results._decision(3.00, 2.50) == "0.50%p 인하"


def _obs(d, v):
    return {"date": d, "value": str(v), "realtime_start": d}


def test_fomc_reads_the_day_after(monkeypatch):
    """FRED 는 새 목표범위를 결정 '다음 날'부터 적는다.

    2026-09-16 인상 실제 기록: 9/16 까지 3.50~3.75, 9/17 부터 3.75~4.00.
    결정일 당일을 '결정 뒤'로 치면 동결로 잘못 읽는다 — 실제로 그럴 뻔했다.
    """
    lo = [_obs("2026-09-18", 3.75), _obs("2026-09-17", 3.75),
          _obs("2026-09-16", 3.50), _obs("2026-09-15", 3.50)]
    hi = [_obs("2026-09-18", 4.00), _obs("2026-09-17", 4.00),
          _obs("2026-09-16", 3.75), _obs("2026-09-15", 3.75)]
    monkeypatch.setattr(results, "_fred", lambda sid, key, n=14: lo if sid == "DFEDTARL" else hi)
    monkeypatch.setattr(results, "FOMC_DECISION_DAYS", ["2026-09-16"])

    got = results.fomc(date(2026, 9, 18), "key")
    assert len(got) == 1 and not got[0].pending
    assert got[0].lines == ["기준금리 목표범위 3.75~4.00% · 0.25%p 인상"]


def test_fomc_pending_until_next_day_is_published(monkeypatch):
    lo = [_obs("2026-09-16", 3.50), _obs("2026-09-15", 3.50)]
    hi = [_obs("2026-09-16", 3.75), _obs("2026-09-15", 3.75)]
    monkeypatch.setattr(results, "_fred", lambda sid, key, n=14: lo if sid == "DFEDTARL" else hi)
    monkeypatch.setattr(results, "FOMC_DECISION_DAYS", ["2026-09-16"])

    got = results.fomc(date(2026, 9, 17), "key")
    assert len(got) == 1 and got[0].pending and got[0].lines == []


def test_mark_shown_keeps_pending_unmarked_and_prunes_old():
    today = date(2026, 9, 25)
    state = {"market_key": "x", "results_shown": {"old": "2026-07-01", "keep": "2026-09-20"}}
    items = [{"key": "earn:COST:2026Q4", "pending": False},
             {"key": "earn:FDX:2027Q1", "pending": True}]
    new = results.mark_shown(state, items, today)
    shown = new["results_shown"]
    assert shown["earn:COST:2026Q4"] == "2026-09-25"
    assert "earn:FDX:2027Q1" not in shown          # 수치가 들어오면 그때 실어야 한다
    assert "old" not in shown and shown["keep"] == "2026-09-20"
    assert new["market_key"] == "x"                # 다른 상태 값은 그대로
