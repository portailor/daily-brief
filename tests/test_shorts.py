from pathlib import Path

from brief.shorts import render as R
from brief.shorts.script import Segment, _index_phrase, _join, haeyo


def _lines():
    a = R.Line(0, "a", Path("a"), dur=1.5, start=0.5)
    b = R.Line(1, "b", Path("b"), dur=2.5, start=2.5)
    return [a, b]


def test_pose_talks_waits_and_surprises():
    segs = [Segment("", [], "a"), Segment("", [], "b", "kr", surprise=True)]
    lines = _lines()
    assert R._pose(0.1, lines, segs, 0) == "smile"             # 시작 전 대기
    mouths = {R._pose(1.0, lines, segs, f) for f in range(8)}
    assert mouths == {"talk_open", "talk_closed"}               # 말하는 동안 번갈아
    assert R._pose(2.2, lines, segs, 0) == "smile"             # 이슈 사이 대기
    assert R._pose(2.6, lines, segs, 0) == "surprised"         # 놀랄 구간 시작
    assert R._pose(2.5 + R.SURPRISE_SEC + 0.1, lines, segs, 0).startswith("talk")


def test_index_speech_signs_and_bp():
    s = _join([_index_phrase("코스피", "-0.23%"), _index_phrase("코스닥", "+1.05%"),
               _index_phrase("S&P 500", "+0.00%"), _index_phrase("미국 10년물 금리", "+5bp")])
    assert s == ("코스피는 0.23퍼센트 내렸고, 코스닥은 1.05퍼센트 올랐어요. "
                 "S&P 500은 보합이었고, 미국 10년물 금리는 0.05퍼센트포인트 올랐어요.")


def test_haeyo():
    assert haeyo("오늘은 평소와 다른 움직임이 없습니다. 특별히 할 일이 없는 날입니다.") == \
        "오늘은 평소와 다른 움직임이 없어요. 특별히 할 일이 없는 날이에요."
    assert haeyo("가장 높았던 값 근처입니다") == "가장 높았던 값 근처예요"
    assert haeyo("4거래일 연속 상승했습니다") == "4거래일 연속 상승했어요"


def test_fit_drops_low_priority_first(monkeypatch):
    monkeypatch.setattr(R, "MAX_SEC", 12)   # 넷이면 14.8초, 셋이면 11.3초
    segs = [Segment("", [], "x", priority=0), Segment("", [], "x", priority=1),
            Segment("", [], "x", priority=4), Segment("", [], "x", priority=2)]
    from brief.shorts.script import Script
    sc = Script("", "", "up", segs)
    lines = [R.Line(i, "x", Path("x"), dur=3.0) for i in range(4)]
    keep, kept = R._fit(sc, lines)
    assert keep == [0, 1, 3]                                   # priority 4 부터 빠진다
    assert kept[0].start == R.LEAD_SEC
