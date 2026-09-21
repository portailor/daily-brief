"""어제 내놓은 예측을 오늘 데이터로 자동 채점한다.

예측이 (지표, 필드, 부등호, 임계값, 기한)의 구조로 저장돼 있기 때문에
사람이 판단할 여지 없이 기계적으로 O/X가 찍힌다.
이 파일이 이 시스템의 정직성을 담보하는 장치다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from brief import db  # noqa: E402

OPS = {
    ">":  lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<":  lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


@dataclass
class ScoreCard:
    resolved: list[dict]          # 이번에 채점된 것
    hit: int = 0
    miss: int = 0
    void: int = 0

    @property
    def total(self) -> int:
        return self.hit + self.miss

    @property
    def accuracy(self) -> float | None:
        return self.hit / self.total if self.total else None


def trading_days_after(conn, instrument: str, start: str, n: int) -> str | None:
    """start 다음 n번째 거래일을 찾는다. 아직 안 왔으면 None."""
    rows = conn.execute(
        "SELECT trade_date FROM observations "
        "WHERE instrument = ? AND trade_date > ? ORDER BY trade_date LIMIT ?",
        (instrument, start, n)).fetchall()
    return rows[n - 1]["trade_date"] if len(rows) >= n else None


def _field_value(conn, instrument: str, trade_date: str, field: str) -> float | None:
    """채점 대상 값을 observations 또는 metrics에서 꺼낸다."""
    if field == "close":
        row = conn.execute(
            "SELECT close AS v FROM observations WHERE instrument=? AND trade_date=?",
            (instrument, trade_date)).fetchone()
    else:
        row = conn.execute(
            f"SELECT {field} AS v FROM metrics WHERE instrument=? AND trade_date=?",
            (instrument, trade_date)).fetchone()
    return row["v"] if row and row["v"] is not None else None


# rules.py 가 실제로 만들어내는 field 를 전부 담아야 한다.
# 'chg' 가 빠져 있어서 σ·연속일 트리거가 통째로 void 처리되고 있었다.
ALLOWED_FIELDS = {"close", "chg", "chg_pct", "chg_bp", "sigma", "pct_52w",
                  "high_52w", "low_52w", "ma20", "ma60", "ma200",
                  "vs_ma20", "vs_ma200", "streak"}


def score_pending(db_path: Path | str | None = None,
                  config_path: Path | str | None = None) -> ScoreCard:
    """아직 채점 안 된 예측 중 기한이 도래한 것을 전부 처리한다.

    경로 인자는 테스트에서 임시 DB·설정을 넣기 위한 것이다. 평소에는 비워 둔다.
    """
    card = ScoreCard(resolved=[])

    from brief.collect.market import CONFIG, load_instruments
    instruments = load_instruments(Path(config_path) if config_path else CONFIG)
    tracked = sorted({i.id for i in instruments})                      # 지금 settings 에 있는 전부
    excluded = sorted({i.id for i in instruments if not i.can_claim})  # 있지만 문장 근거로 못 쓰는 것

    with db.session(db_path or db.DB_PATH) as conn:
        # ① 정책금리(predict: false)나 선물(futures: true)처럼 예측 대상에서 뺀 지표는
        #    과거에 만들어져 채점까지 끝난 기록도 무효로 돌린다. 적중률을 오염시키기 때문이다.
        if excluded:
            ph = ",".join("?" * len(excluded))
            conn.execute(
                f"UPDATE predictions SET result='void' "
                f"WHERE instrument IN ({ph}) AND (result IS NULL OR result != 'void')",
                excluded)

        # ② settings 에서 통째로 사라진 지표. ①은 목록에 있는 것만 훑으므로 여기는 못 잡는다.
        #    관측치가 더 쌓이지 않아 trading_days_after 가 영영 None 을 돌려주고,
        #    그대로 두면 '미채점'으로 영원히 남아 아래 채점 루프를 매일 헛돌게 한다.
        #    (US03M·US30Y 예측이 지표를 뺀 뒤 일주일 넘게 이렇게 묶여 있었다.)
        #    ①과 달리 이미 채점된 기록은 건드리지 않는다 — 당시에는 정상으로 추적하던
        #    지표였고, 그때 받은 O/X 는 정직하게 얻은 성적이기 때문이다.
        if tracked:
            ph = ",".join("?" * len(tracked))
            cur = conn.execute(
                f"UPDATE predictions SET result='void' "
                f"WHERE instrument NOT IN ({ph}) AND result IS NULL", tracked)
            card.void += cur.rowcount

        pending = conn.execute(
            "SELECT * FROM predictions WHERE result IS NULL ORDER BY made_on, id"
        ).fetchall()

        for p in pending:
            if p["field"] not in ALLOWED_FIELDS:
                conn.execute("UPDATE predictions SET result='void' WHERE id=?", (p["id"],))
                card.void += 1
                continue

            due = trading_days_after(conn, p["instrument"], p["made_on"], p["horizon_days"])
            if due is None:
                continue                              # 아직 기한 전 — 다음 실행에서 다시 본다

            actual = _field_value(conn, p["instrument"], due, p["field"])
            if actual is None:
                conn.execute(
                    "UPDATE predictions SET due_on=?, resolved_on=?, result='void' WHERE id=?",
                    (due, due, p["id"]))
                card.void += 1
                continue

            ok = OPS[p["op"]](actual, p["threshold"])
            result = "hit" if ok else "miss"
            conn.execute(
                "UPDATE predictions SET due_on=?, resolved_on=?, actual=?, result=? WHERE id=?",
                (due, due, actual, result, p["id"]))

            card.resolved.append({
                "claim": p["claim"],
                "instrument": p["instrument"],
                "field": p["field"],
                "op": p["op"],
                "threshold": p["threshold"],
                "actual": actual,
                "result": result,
                "made_on": p["made_on"],
                "resolved_on": due,
                "probability": p["probability"],
            })
            if ok:
                card.hit += 1
            else:
                card.miss += 1

    return card


def record(conn, made_on: str, claim: str, instrument: str, field: str,
           op: str, threshold: float, horizon_days: int = 1,
           probability: float | None = None) -> None:
    """오늘의 예측을 저장한다. 같은 날 같은 조건이면 중복 저장하지 않는다."""
    conn.execute(
        """INSERT OR IGNORE INTO predictions
           (made_on, claim, instrument, field, op, threshold, horizon_days, probability)
           VALUES (?,?,?,?,?,?,?,?)""",
        (made_on, claim, instrument, field, op, threshold, horizon_days, probability))


def track_record(conn, days: int = 90) -> dict:
    """누적 성적. 브리핑 최상단에 박아서 스스로를 감시하게 만든다."""
    row = conn.execute(
        """SELECT
             SUM(result='hit')  AS hit,
             SUM(result='miss') AS miss,
             COUNT(*)           AS n
           FROM predictions
           WHERE result IN ('hit','miss')
             AND made_on >= date('now', ?)""",
        (f"-{days} days",)).fetchone()

    hit = row["hit"] or 0
    miss = row["miss"] or 0
    total = hit + miss

    # 확률을 명시한 예측만 따로 — 캘리브레이션 확인용
    cal = conn.execute(
        """SELECT AVG(probability) AS avg_p, AVG(result='hit') AS actual_rate, COUNT(*) AS n
           FROM predictions
           WHERE result IN ('hit','miss') AND probability IS NOT NULL
             AND made_on >= date('now', ?)""",
        (f"-{days} days",)).fetchone()

    return {
        "hit": hit,
        "miss": miss,
        "total": total,
        "accuracy": hit / total if total else None,
        "stated_prob": cal["avg_p"],
        "actual_rate": cal["actual_rate"],
        "calibration_n": cal["n"] or 0,
        "window_days": days,
    }


if __name__ == "__main__":
    card = score_pending()
    print(f"채점 완료: 적중 {card.hit} / 빗나감 {card.miss} / 무효 {card.void}")
    for r in card.resolved:
        mark = "O" if r["result"] == "hit" else "X"
        print(f"  [{mark}] {r['claim']}")
        print(f"       실제 {r['actual']:.3f} (기준 {r['op']} {r['threshold']:.3f})")

    with db.connect() as c:
        tr = track_record(c)
    if tr["total"]:
        print(f"\n최근 {tr['window_days']}일 누적: "
              f"{tr['hit']}/{tr['total']} = {tr['accuracy']:.0%}")
        if tr["calibration_n"]:
            print(f"  캘리브레이션: 평균 제시확률 {tr['stated_prob']:.0%} "
                  f"vs 실제 적중률 {tr['actual_rate']:.0%} (n={tr['calibration_n']})")
    else:
        print("\n아직 채점된 예측이 없습니다. 내일부터 쌓입니다.")
