"""SQLite 저장소. 모든 원시 데이터와 예측 기록이 여기 쌓인다.

핵심은 predictions 테이블이다. 예측을 '문장'이 아니라
(지표, 필드, 부등호, 임계값, 기한)의 구조로 저장하기 때문에
다음날 사람 손 없이 자동으로 채점된다.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "brief.db"

SCHEMA = """
-- 원시 시세: 수집한 그대로, 가공 없음
CREATE TABLE IF NOT EXISTS observations (
    trade_date   TEXT NOT NULL,
    instrument   TEXT NOT NULL,
    open         REAL,
    high         REAL,
    low          REAL,
    close        REAL NOT NULL,
    volume       REAL,
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (trade_date, instrument)
);

-- 파생 지표: observations에서 결정론적으로 계산된 값 (LLM 관여 없음)
CREATE TABLE IF NOT EXISTS metrics (
    trade_date   TEXT NOT NULL,
    instrument   TEXT NOT NULL,
    chg          REAL,   -- 전일 대비 절대 변화
    chg_pct      REAL,   -- 전일 대비 %  (kind=price)
    chg_bp       REAL,   -- 전일 대비 bp (kind=rate)
    sigma        REAL,   -- 오늘 변동이 평소 대비 몇 σ인가
    pct_52w      REAL,   -- 52주 밴드 내 위치 0~100
    high_52w     REAL,
    low_52w      REAL,
    ma20         REAL,
    ma60         REAL,
    ma200        REAL,
    vs_ma20      REAL,   -- 20일선 대비 % (양수면 위)
    vs_ma200     REAL,
    streak       INTEGER,-- 같은 방향 연속일 (+3 = 3일 연속 상승)
    PRIMARY KEY (trade_date, instrument)
);

-- 투자자별 수급 (KRX)
CREATE TABLE IF NOT EXISTS flows (
    trade_date   TEXT NOT NULL,
    market       TEXT NOT NULL,   -- KOSPI / KOSDAQ
    investor     TEXT NOT NULL,   -- 외국인 / 기관합계 / 개인
    net_buy      REAL NOT NULL,   -- 순매수 대금 (원)
    source       TEXT NOT NULL,
    PRIMARY KEY (trade_date, market, investor)
);

-- 예측 기록 + 자동 채점
--   "10Y가 4.90 위로 마감하면" → instrument=US10Y, field=close, op='>', threshold=4.90
CREATE TABLE IF NOT EXISTS predictions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    made_on      TEXT NOT NULL,   -- 예측을 내놓은 날
    claim        TEXT NOT NULL,   -- 사람이 읽는 문장
    instrument   TEXT NOT NULL,
    field        TEXT NOT NULL,   -- close / chg_pct / sigma / net_buy
    op           TEXT NOT NULL,   -- > >= < <=
    threshold    REAL NOT NULL,
    horizon_days INTEGER NOT NULL DEFAULT 1,
    probability  REAL,            -- LLM이 명시한 확률 0~1
    due_on       TEXT,            -- 채점 예정일 (거래일 기준으로 채워짐)
    resolved_on  TEXT,
    actual       REAL,
    result       TEXT,            -- hit / miss / void
    UNIQUE (made_on, instrument, field, op, threshold, horizon_days)
);

-- 발행 이력
CREATE TABLE IF NOT EXISTS briefs (
    brief_date   TEXT PRIMARY KEY,
    html_path    TEXT,
    public_url   TEXT,
    kakao_sent   INTEGER DEFAULT 0,
    generated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_inst  ON observations(instrument, trade_date);
CREATE INDEX IF NOT EXISTS idx_pred_due  ON predictions(due_on, result);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(path: Path | str = DB_PATH) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def session(path: Path | str = DB_PATH):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


LATEST_SNAPSHOT_SQL = """
SELECT m.*, o.close
FROM metrics m
JOIN observations o
  ON m.trade_date = o.trade_date AND m.instrument = o.instrument
JOIN (SELECT instrument, MAX(trade_date) AS d FROM metrics GROUP BY instrument) x
  ON m.instrument = x.instrument AND m.trade_date = x.d
"""


def latest_snapshot(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """지표별 '가장 최근 거래일' 한 줄씩.

    미국장은 한국시간 새벽에 마감하므로 국내 지표와 거래일이 하루 어긋난다.
    하나의 날짜로 조인하면 미국 지수와 금리가 통째로 빠지기 때문에,
    지표마다 자기 최신일을 쓴다.
    """
    return conn.execute(LATEST_SNAPSHOT_SQL).fetchall()


def purge_unfinished(conn: sqlite3.Connection) -> int:
    """마감되지 않은 거래일(오늘 이후)과 주말 날짜 행을 지운다.

    수집기는 이미 이런 행을 받지 않지만, 이전 버전이 저장해 둔 행이
    로컬 DB 에 남아 있으면 σ와 채점이 계속 오염되므로 한 번 더 걸러낸다.
    월간 계열은 매월 1일로 찍혀 주말일 수 있어 제외한다.
    """
    from brief.clock import today_kst

    today = today_kst().isoformat()
    monthly = ("CPI_YOY", "UNRATE")
    weekend = "strftime('%w', trade_date) IN ('0','6')"

    n = 0
    for table in ("observations", "metrics"):
        n += conn.execute(
            f"DELETE FROM {table} WHERE trade_date >= ? "
            f"OR ({weekend} AND instrument NOT IN (?, ?))",
            (today, *monthly)).rowcount
    n += conn.execute(
        f"DELETE FROM flows WHERE trade_date >= ? OR {weekend}", (today,)).rowcount
    return n


# ── 예측 기록 보존 ───────────────────────────────────────────
# 클라우드(GitHub Actions)에서는 매 실행이 빈 서버에서 시작하므로
# DB가 남지 않는다. 시세·지표는 매번 API에서 다시 받으면 그만이지만,
# 예측 기록만은 잃으면 적중률이 매일 0에서 다시 시작한다.
# 그래서 이것만 CSV로 떠서 저장소에 커밋한다 (수백 줄이라 가볍다).

PREDICTIONS_CSV = Path(__file__).resolve().parent.parent / "data" / "predictions.csv"

_PRED_COLS = ["made_on", "claim", "instrument", "field", "op", "threshold",
              "horizon_days", "probability", "due_on", "resolved_on",
              "actual", "result"]


def export_predictions(conn: sqlite3.Connection,
                       path: Path | str = PREDICTIONS_CSV) -> int:
    import csv

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        f"SELECT {','.join(_PRED_COLS)} FROM predictions ORDER BY made_on, id"
    ).fetchall()

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(_PRED_COLS)
        w.writerows([tuple(r[c] for c in _PRED_COLS) for r in rows])
    return len(rows)


def import_predictions(conn: sqlite3.Connection,
                       path: Path | str = PREDICTIONS_CSV) -> int:
    """CSV의 기록을 DB로 되살린다. 이미 있는 예측은 건드리지 않는다."""
    import csv

    path = Path(path)
    if not path.exists():
        return 0

    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    n = 0
    for r in rows:
        vals = [r.get(c) or None for c in _PRED_COLS]
        cur = conn.execute(
            f"""INSERT OR IGNORE INTO predictions ({','.join(_PRED_COLS)})
                VALUES ({','.join('?' * len(_PRED_COLS))})""", vals)
        n += cur.rowcount
    return n


def last_trade_date(conn: sqlite3.Connection, instrument: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(trade_date) AS d FROM observations WHERE instrument = ?",
        (instrument,),
    ).fetchone()
    return row["d"] if row else None


if __name__ == "__main__":
    init()
    with connect() as c:
        tables = [r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"DB 초기화 완료: {DB_PATH}")
    print("테이블:", ", ".join(tables))
