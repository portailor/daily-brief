"""예측 자동 채점.

이 파일이 지키는 것은 정확도가 아니라 정직성이다. 채점이 조용히 멈추거나
기록이 슬그머니 사라지면 적중률은 늘 좋아 보이게 된다.
"""
from __future__ import annotations

import sqlite3

import pytest

from brief import db
from brief.score import scorer


# ── 테스트용 저장소 ──────────────────────────────────────────
SETTINGS = """
timezone: Asia/Seoul
instruments:
  - id: KOSPI
    name: 코스피
    ticker: "^KS11"
    kind: price
    group: kr_equity
  - id: KR_BASE
    name: 한국 기준금리
    kind: rate
    group: kr_rates
    source: ecos
    predict: false
  - id: GOLD
    name: 국제 금
    ticker: "GC=F"
    kind: price
    group: commodity
    futures: true
analysis:
  sigma_window: 60
"""


@pytest.fixture
def repo(tmp_path):
    """빈 DB 와 설정 파일 한 벌. 실제 저장소는 건드리지 않는다."""
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(SETTINGS, encoding="utf-8")
    dbp = tmp_path / "brief.db"
    db.init(dbp)
    return dbp, cfg


def add_observation(conn, instrument: str, trade_date: str, close: float) -> None:
    conn.execute(
        "INSERT INTO observations (trade_date, instrument, close, source, fetched_at)"
        " VALUES (?,?,?,'test','2026-01-01T00:00:00')",
        (trade_date, instrument, close))


def add_metric(conn, instrument: str, trade_date: str, **fields) -> None:
    cols = ["trade_date", "instrument", *fields]
    vals = [trade_date, instrument, *fields.values()]
    conn.execute(
        f"INSERT INTO metrics ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        vals)


def add_prediction(conn, **kw) -> None:
    kw.setdefault("horizon_days", 1)
    scorer.record(conn, **kw)


def results(dbp) -> dict[str, str | None]:
    """claim -> result 로 뽑아 본다."""
    with db.connect(dbp) as c:
        return {r["claim"]: r["result"]
                for r in c.execute("SELECT claim, result FROM predictions")}


# ── 기본 동작 ────────────────────────────────────────────────
def test_기한이_지난_예측은_hit_miss_로_채점된다(repo):
    dbp, cfg = repo
    with db.session(dbp) as c:
        add_observation(c, "KOSPI", "2026-03-02", 6900.0)
        add_observation(c, "KOSPI", "2026-03-03", 6850.0)
        add_prediction(c, made_on="2026-03-02", claim="코스피가 6,900 아래로 마감",
                       instrument="KOSPI", field="close", op="<", threshold=6900.0)
        add_prediction(c, made_on="2026-03-02", claim="코스피가 7,000 위로 마감",
                       instrument="KOSPI", field="close", op=">", threshold=7000.0)

    card = scorer.score_pending(db_path=dbp, config_path=cfg)

    assert (card.hit, card.miss) == (1, 1)
    assert results(dbp) == {
        "코스피가 6,900 아래로 마감": "hit",     # 6850 < 6900
        "코스피가 7,000 위로 마감": "miss",      # 6850 > 7000 이 아니다
    }


def test_기한_전이면_손대지_않고_다음_실행으로_넘긴다(repo):
    dbp, cfg = repo
    with db.session(dbp) as c:
        add_observation(c, "KOSPI", "2026-03-02", 6900.0)   # 다음 거래일이 아직 없다
        add_prediction(c, made_on="2026-03-02", claim="아직 기한 전",
                       instrument="KOSPI", field="close", op="<", threshold=6900.0)

    card = scorer.score_pending(db_path=dbp, config_path=cfg)

    assert (card.hit, card.miss, card.void) == (0, 0, 0)
    assert results(dbp) == {"아직 기한 전": None}


def test_close_가_아닌_필드는_metrics_에서_읽는다(repo):
    dbp, cfg = repo
    with db.session(dbp) as c:
        add_observation(c, "KOSPI", "2026-03-02", 6900.0)
        add_observation(c, "KOSPI", "2026-03-03", 6850.0)
        add_metric(c, "KOSPI", "2026-03-03", chg=-50.0, sigma=-1.2)
        add_prediction(c, made_on="2026-03-02", claim="급등을 되돌림",
                       instrument="KOSPI", field="chg", op="<", threshold=0.0)

    scorer.score_pending(db_path=dbp, config_path=cfg)

    assert results(dbp) == {"급등을 되돌림": "hit"}


def test_기한은_달력일이_아니라_거래일로_센다(repo):
    """주말·휴장일을 건너뛴다. horizon 2 면 관측치 기준 두 번째 날이다."""
    dbp, cfg = repo
    with db.session(dbp) as c:
        for d, v in [("2026-03-02", 6900.0), ("2026-03-06", 6800.0), ("2026-03-09", 6700.0)]:
            add_observation(c, "KOSPI", d, v)
        add_prediction(c, made_on="2026-03-02", claim="이틀 뒤 6,750 아래",
                       instrument="KOSPI", field="close", op="<", threshold=6750.0,
                       horizon_days=2)

    scorer.score_pending(db_path=dbp, config_path=cfg)

    with db.connect(dbp) as c:
        row = c.execute("SELECT due_on, actual FROM predictions").fetchone()
    assert row["due_on"] == "2026-03-09"        # 03-06 이 아니라 두 번째 거래일
    assert row["actual"] == 6700.0


# ── 무효 처리 ────────────────────────────────────────────────
def test_예측_대상이_아닌_지표는_채점된_기록까지_무효로_돌린다(repo):
    """정책금리(predict: false)·선물(futures: true)은 문장 근거로 쓰면 안 된다.

    나중에 그렇게 표시했다면 과거 성적도 되돌린다. 남겨 두면 적중률이 오염된다.
    """
    dbp, cfg = repo
    with db.session(dbp) as c:
        for inst in ("KR_BASE", "GOLD"):
            add_observation(c, inst, "2026-03-02", 100.0)
            add_observation(c, inst, "2026-03-03", 90.0)
            add_prediction(c, made_on="2026-03-02", claim=f"{inst} 하락",
                           instrument=inst, field="close", op="<", threshold=95.0)
        # 이미 hit 로 채점돼 있던 상황을 만든다
        c.execute("UPDATE predictions SET result='hit'")

    scorer.score_pending(db_path=dbp, config_path=cfg)

    assert results(dbp) == {"KR_BASE 하락": "void", "GOLD 하락": "void"}


def test_settings_에서_제거된_지표의_예측은_영원히_남지_않고_무효가_된다(repo):
    """지표를 빼면 관측치가 더 쌓이지 않아 기한이 영영 오지 않는다.

    그대로 두면 '미채점'으로 남아 매일 채점 루프를 헛돌게 한다.
    실제로 US03M·US30Y 예측이 지표 제거 후 일주일 넘게 이렇게 묶여 있었다.
    """
    dbp, cfg = repo
    with db.session(dbp) as c:
        add_observation(c, "US30Y", "2026-03-02", 95.0)     # 설정에 없는 지표
        add_prediction(c, made_on="2026-03-02", claim="빠진 지표 예측",
                       instrument="US30Y", field="pct_52w", op="<", threshold=92.0)
        add_observation(c, "KOSPI", "2026-03-02", 6900.0)
        add_prediction(c, made_on="2026-03-02", claim="살아있는 지표 예측",
                       instrument="KOSPI", field="close", op="<", threshold=6900.0)

    card = scorer.score_pending(db_path=dbp, config_path=cfg)

    got = results(dbp)
    assert got["빠진 지표 예측"] == "void"      # 무한 대기하지 않는다
    assert got["살아있는 지표 예측"] is None    # 기한 전일 뿐이므로 건드리지 않는다
    assert card.void == 1


def test_제거된_지표라도_이미_채점된_성적은_보존한다(repo):
    """추적하던 당시에 정직하게 얻은 O/X 다. 지표를 뺐다고 지우면 성적을 세탁하는 셈이다."""
    dbp, cfg = repo
    with db.session(dbp) as c:
        add_prediction(c, made_on="2026-03-02", claim="빠진 지표의 옛 성적",
                       instrument="US30Y", field="close", op="<", threshold=95.0)
        c.execute("UPDATE predictions SET result='miss', actual=96.0")

    scorer.score_pending(db_path=dbp, config_path=cfg)

    assert results(dbp) == {"빠진 지표의 옛 성적": "miss"}


def test_모르는_필드는_무효_처리한다(repo):
    dbp, cfg = repo
    with db.session(dbp) as c:
        add_observation(c, "KOSPI", "2026-03-02", 6900.0)
        add_observation(c, "KOSPI", "2026-03-03", 6850.0)
        add_prediction(c, made_on="2026-03-02", claim="이상한 필드",
                       instrument="KOSPI", field="rsi", op="<", threshold=30.0)

    card = scorer.score_pending(db_path=dbp, config_path=cfg)

    assert card.void == 1
    assert results(dbp) == {"이상한 필드": "void"}


def test_기한은_왔는데_값이_없으면_무효다(repo):
    """추측해서 채점하지 않는다."""
    dbp, cfg = repo
    with db.session(dbp) as c:
        add_observation(c, "KOSPI", "2026-03-02", 6900.0)
        add_observation(c, "KOSPI", "2026-03-03", 6850.0)   # metrics 는 비어 있다
        add_prediction(c, made_on="2026-03-02", claim="지표값 없음",
                       instrument="KOSPI", field="sigma", op="<", threshold=0.0)

    card = scorer.score_pending(db_path=dbp, config_path=cfg)

    assert card.void == 1
    assert results(dbp) == {"지표값 없음": "void"}


def test_같은_날_같은_조건은_중복_저장되지_않는다(repo):
    dbp, _ = repo
    with db.session(dbp) as c:
        for _ in range(3):
            add_prediction(c, made_on="2026-03-02", claim="같은 예측",
                           instrument="KOSPI", field="close", op="<", threshold=6900.0)

    with db.connect(dbp) as c:
        assert c.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"] == 1
