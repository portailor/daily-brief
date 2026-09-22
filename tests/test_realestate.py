"""부동산 — 'N주 연속'이 틀리면 기사와 다른 숫자가 나간다. 네트워크는 쓰지 않는다."""
from brief.collect.realestate import streak_of


def test_streak_counts_until_flat_week():
    # 보합(0.00%) 이후 연속 상승 3주
    assert streak_of([0.10, 0.0, 0.05, 0.12, 0.16]) == (0.16, 3)


def test_streak_breaks_on_direction_change():
    # 강남구처럼 상승하다 하락으로 돌아선 경우 — 하락만 센다
    assert streak_of([0.20, 0.10, -0.05, -0.20, -0.36]) == (-0.36, 3)


def test_flat_this_week_is_zero():
    assert streak_of([0.10, 0.20, 0.001]) == (0.0, 0)


def test_rounding_follows_published_two_decimals():
    # 0.004% 는 발표 기준 0.00% 보합이라 연속을 끊는다
    assert streak_of([0.10, 0.004, 0.10, 0.10]) == (0.10, 2)
