"""예측 기록 합치기.

발송 대기 중에 다른 커밋이 올라오면 rebase 충돌이 난다. 예전에는
`-X theirs` 로 한쪽을 통째로 골랐고, 그러면 반대쪽에서 찍힌 O/X 가
경고 없이 사라졌다. 여기서 지키는 것은 '기록이 줄어들지 않는다'이다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import merge_predictions as mp


def row(made_on="2026-03-02", instrument="KOSPI", threshold="6900.0",
        result="", claim="코스피가 6,900 아래로 마감", **over):
    r = {"made_on": made_on, "claim": claim, "instrument": instrument,
         "field": "close", "op": "<", "threshold": threshold,
         "horizon_days": "1", "probability": "0.5", "due_on": "",
         "resolved_on": "", "actual": "", "result": result}
    r.update(over)
    return r


def test_양쪽에만_있는_예측이_전부_남는다():
    remote = [row(instrument="KOSPI")]
    local = [row(instrument="USDKRW")]

    got = mp.merge(remote, local)

    assert {r["instrument"] for r in got} == {"KOSPI", "USDKRW"}


def test_채점된_쪽을_남긴다():
    """미채점 행에는 아직 아무 정보가 없다."""
    remote = [row(result="")]
    local = [row(result="hit", actual="6850.0", resolved_on="2026-03-03")]

    got = mp.merge(remote, local)

    assert len(got) == 1
    assert got[0]["result"] == "hit"
    assert got[0]["actual"] == "6850.0"


def test_반대_방향도_마찬가지다():
    remote = [row(result="miss", actual="6950.0")]
    local = [row(result="")]

    got = mp.merge(remote, local)

    assert got[0]["result"] == "miss"


def test_양쪽_다_채점됐고_결과가_다르면_이미_발행된_쪽을_남긴다():
    """한 번 내보낸 O/X 를 나중에 덮어쓰지 않는다."""
    remote = [row(result="hit", actual="6850.0")]
    local = [row(result="miss", actual="6999.0")]

    got = mp.merge(remote, local)

    assert got[0]["result"] == "hit"
    assert got[0]["actual"] == "6850.0"


def test_같은_날_같은_지표라도_조건이_다르면_다른_예측이다():
    """키가 뭉개지면 서로 다른 예측이 하나로 합쳐진다."""
    remote = [row(threshold="6900.0")]
    local = [row(threshold="7000.0", claim="코스피가 7,000 아래로 마감")]

    got = mp.merge(remote, local)

    assert len(got) == 2
    assert {r["threshold"] for r in got} == {"6900.0", "7000.0"}


def test_합쳐도_기록이_줄어들지_않는다():
    """이 테스트 하나가 이 파일의 목적이다."""
    remote = [row(instrument=f"I{i}") for i in range(5)]
    local = [row(instrument=f"I{i}") for i in range(3, 8)]

    got = mp.merge(remote, local)

    assert len(got) >= max(len(remote), len(local))
    assert len(got) == 8                      # I0~I7


def test_한쪽이_비어_있어도_동작한다():
    assert mp.merge([], [row()]) != []
    assert mp.merge([row()], []) != []
    assert mp.merge([], []) == []


def test_made_on_순으로_정렬해_내보낸다():
    """매일 커밋되는 파일이다. 순서가 흔들리면 diff 가 통째로 뒤집힌다."""
    got = mp.merge([row(made_on="2026-03-05"), row(made_on="2026-03-02")], [])
    assert [r["made_on"] for r in got] == ["2026-03-02", "2026-03-05"]


def test_파일로_주고받는_경로가_동작한다(tmp_path):
    a, b, out = tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "out.csv"
    mp.write([row(result="hit")], a)
    mp.write([row(result=""), row(instrument="DXY")], b)

    assert mp.main(["merge_predictions.py", str(a), str(b), str(out)]) == 0

    rows = mp.read(out)
    assert len(rows) == 2
    kospi = next(r for r in rows if r["instrument"] == "KOSPI")
    assert kospi["result"] == "hit"           # 채점 결과가 살아남는다


def test_없는_파일은_빈_목록으로_읽는다(tmp_path):
    assert mp.read(tmp_path / "없음.csv") == []


def test_키가_DB_의_UNIQUE_제약과_같다():
    """둘이 어긋나면 합친 결과를 DB 가 거부하거나 중복이 생긴다."""
    schema = (Path(__file__).resolve().parent.parent / "brief" / "db.py").read_text(encoding="utf-8")
    line = next(l for l in schema.splitlines() if "UNIQUE (" in l)
    in_db = [c.strip() for c in line.split("(", 1)[1].rstrip(")").split(",")]
    assert list(mp.KEY) == in_db
