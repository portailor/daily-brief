"""날짜 판정.

주말 중복 발송과 날짜 오표기가 실제로 났던 자리다. 달력을 손으로 계산하는
코드는 한 번 맞으면 영원히 맞는 게 아니라, 고칠 때마다 다시 틀린다.
"""
from __future__ import annotations

from datetime import date

import pytest

from brief import clock


# 2026-03-02 는 월요일. 아래 테스트 전체가 이 주를 쓴다.
MON, TUE, WED, THU, FRI = (date(2026, 3, d) for d in (2, 3, 4, 5, 6))
SAT, SUN = date(2026, 3, 7), date(2026, 3, 8)
NEXT_MON = date(2026, 3, 9)


def test_기준주의_요일이_실제로_맞다():
    """테스트가 딛고 선 전제부터 확인한다."""
    assert MON.weekday() == 0 and FRI.weekday() == 4
    assert SAT.weekday() == 5 and SUN.weekday() == 6


@pytest.mark.parametrize("d", [MON, TUE, WED, THU, FRI])
def test_지난_평일은_마감된_거래일이다(d):
    assert clock.is_finished_session(d, today=NEXT_MON) is True


@pytest.mark.parametrize("d", [SAT, SUN])
def test_주말은_거래일이_아니다(d):
    """공식 계열 지표는 주말에도 날짜가 찍힌다. 그대로 받으면 토요일에 거래가 있었던 셈이 된다."""
    assert clock.is_finished_session(d, today=NEXT_MON) is False


def test_오늘은_아직_끝나지_않았다():
    """아침에 도는 시스템이다. '오늘' 날짜의 값은 전부 장중이거나 미래다."""
    assert clock.is_finished_session(WED, today=WED) is False


def test_미래_날짜는_거래일이_아니다():
    assert clock.is_finished_session(FRI, today=WED) is False


def test_어제는_평일이면_거래일이다():
    """한국시간 아침 기준으로 미국장의 '어제' 거래는 이미 끝나 있다."""
    assert clock.is_finished_session(TUE, today=WED) is True


def test_문자열_날짜도_같은_결과를_준다():
    assert clock.is_finished_session("2026-03-06", today=NEXT_MON) is True
    assert clock.is_finished_session("2026-03-07", today=NEXT_MON) is False


def test_월요일_아침에는_금요일이_직전_거래일이다():
    """주말을 건너뛴다. 토·일이 최근 날짜로 뽑히면 안 된다."""
    assert clock.is_finished_session(FRI, today=MON + (NEXT_MON - MON)) is True
    assert clock.is_finished_session(SAT, today=NEXT_MON) is False
    assert clock.is_finished_session(SUN, today=NEXT_MON) is False


# ── 표기 ────────────────────────────────────────────────────
@pytest.mark.parametrize("d,want", [
    (MON, "3/2(월)"),
    (FRI, "3/6(금)"),
    (SUN, "3/8(일)"),
    (date(2026, 12, 25), "12/25(금)"),
])
def test_짧은_날짜_표기(d, want):
    assert clock.label(d) == want


def test_긴_날짜_표기():
    assert clock.label_long(date(2026, 9, 11)) == "2026년 9월 11일 (금)"


def test_표기도_문자열_입력을_받는다():
    assert clock.label("2026-03-02") == "3/2(월)"
    assert clock.label_long("2026-03-02") == "2026년 3월 2일 (월)"


def test_요일_문자열이_월요일부터_시작한다():
    """WEEKDAY_KR 의 순서가 date.weekday() 와 어긋나면 모든 표기가 조용히 밀린다."""
    assert clock.WEEKDAY_KR[0] == "월"
    assert clock.WEEKDAY_KR[6] == "일"
    assert len(clock.WEEKDAY_KR) == 7


def test_today_kst_는_한국시간_날짜를_준다():
    """UTC 기준으로 계산하면 한국시간 오전 9시 이전에 하루가 밀린다."""
    from datetime import datetime
    assert clock.today_kst() == datetime.now(clock.KST).date()
