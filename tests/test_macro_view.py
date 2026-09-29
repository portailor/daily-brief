from brief.interpret import macro_view as mv


def test_episodes_merge_consecutive_days():
    d = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-03-01", "2020-03-02"]
    assert mv._episodes(d) == ["2020-01-02", "2020-03-01"]


def test_forward_return_and_stats():
    series = [(f"2020-01-{i:02d}", 100 + i) for i in range(1, 31)]
    assert round(mv._forward(series, "2020-01-01"), 2) == round((121 / 101 - 1) * 100, 2)
    assert mv._forward(series, "2020-01-25") is None            # 20거래일 뒤가 아직 없다
    assert mv._stats([1.0] * 7) is None                         # 표본이 적으면 통계 없음
    st = mv._stats([1.0, -1.0, 2.0, 3.0, -2.0, 4.0, 5.0, 6.0])
    assert st["n"] == 8 and st["up"] == 75


def test_josa_for_numbers():
    assert mv._ro("7,764.70") == "으로" and mv._ro("4.960%") == "로" and mv._ro("1,344.64") == "로"
