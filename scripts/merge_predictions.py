"""충돌한 예측 기록 두 판을 합친다.

브리핑 실행은 발송 시각까지 몇 시간을 기다린다. 그 사이에 다른 커밋이
올라오면 rebase 가 필요한데, 예전에는 `-X theirs` 로 한쪽을 통째로 골랐다.
docs/ 는 매번 새로 만드니 그래도 되지만 예측 기록은 다르다 — 한쪽을 고르면
다른 쪽에서 찍힌 O/X 가 경고 없이 사라진다. 적중률을 담보로 한 파일에서
기록이 조용히 없어지는 것은 가장 나쁜 실패다.

그래서 고르지 않고 합친다. 규칙은 두 줄이다.

  1. 채점된 쪽을 남긴다. 미채점은 아직 아무 정보가 없다.
  2. 양쪽 다 채점됐고 결과가 다르면 이미 발행된 쪽(remote)을 남긴다.
     한 번 내보낸 O/X 를 나중에 덮어쓰지 않는다.

사용법:
    python scripts/merge_predictions.py <remote.csv> <local.csv> <out.csv>
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

COLS = ["made_on", "claim", "instrument", "field", "op", "threshold",
        "horizon_days", "probability", "due_on", "resolved_on",
        "actual", "result"]

# DB 의 UNIQUE 제약과 같은 키. 같은 예측인지 판단하는 기준이 어긋나면
# 합치는 순간 중복이 생기거나 서로 다른 예측이 뭉개진다.
KEY = ("made_on", "instrument", "field", "op", "threshold", "horizon_days")


def key_of(row: dict) -> tuple:
    return tuple((row.get(c) or "").strip() for c in KEY)


def is_graded(row: dict) -> bool:
    return bool((row.get("result") or "").strip())


def read(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def merge(remote: list[dict], local: list[dict]) -> list[dict]:
    """remote = 이미 올라가 있는 판, local = 이번 실행이 만든 판."""
    out: dict[tuple, dict] = {key_of(r): r for r in remote}

    for row in local:
        k = key_of(row)
        have = out.get(k)
        if have is None:                       # 이번 실행에서 새로 생긴 예측
            out[k] = row
        elif not is_graded(have) and is_graded(row):
            out[k] = row                       # 이번 실행이 채점했다
        # 그 밖의 경우는 remote 를 그대로 둔다 (규칙 2)

    return sorted(out.values(), key=lambda r: (r.get("made_on") or "", key_of(r)))


def write(rows: list[dict], path: str | Path) -> int:
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2

    remote, local, out = argv[1], argv[2], argv[3]
    rows = merge(read(remote), read(local))
    n = write(rows, out)
    graded = sum(1 for r in rows if is_graded(r))
    print(f"예측 기록 합침: {n}건 (채점 완료 {graded}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
