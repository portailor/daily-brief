from brief.shorts import render as R
from brief.shorts.script import Segment, _say_tiles


def test_pose_talks_waits_and_surprises():
    segs = [Segment("a", "a"), Segment("b", "b", "tiles", surprise=True)]
    spans = [(0.5, 2.0), (2.5, 5.0)]
    assert R._pose(0.1, spans, segs, 0) == "smile"             # 시작 전 대기
    mouths = {R._pose(1.0, spans, segs, f) for f in range(8)}
    assert mouths == {"talk_open", "talk_closed"}               # 말하는 동안 번갈아
    assert R._pose(2.2, spans, segs, 0) == "smile"             # 이슈 사이 대기
    assert R._pose(2.6, spans, segs, 0) == "surprised"         # 놀랄 구간 시작
    assert R._pose(2.5 + R.SURPRISE_SEC + 0.1, spans, segs, 0).startswith("talk")


def test_tiles_speech_signs():
    payload = {"tiles": [{"id": "KOSPI", "change": "-0.23%"}, {"id": "KOSDAQ", "change": "+1.05%"},
                         {"id": "SPX", "change": "+0.00%"}]}
    s = _say_tiles(payload, ("KOSPI", "KOSDAQ", "SPX"))
    assert s == ("코스피는 0.23퍼센트 내렸고, 코스닥은 1.05퍼센트 올랐습니다. "
                 "S&P 500은 보합이었습니다.")
