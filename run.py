"""매일 아침 실행되는 진입점.

  python run.py              수집 → 분석 → 채점 → 리포트 → 카톡 발송
  python run.py --no-send    발송 없이 리포트만 (테스트용)
  python run.py --no-fetch   수집 건너뛰고 기존 데이터로 리포트만

설계 원칙: 한 단계가 실패해도 나머지는 진행한다.
수급 수집이 막혔다고 브리핑 전체가 안 오면 안 된다.
대신 무엇이 실패했는지 리포트 하단과 로그에 남긴다.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from brief import db                                    # noqa: E402
from brief.analyze import metrics                       # noqa: E402
from brief.collect import flows, macro, market          # noqa: E402
from brief.render import kakao_text, report             # noqa: E402

LOG_PATH = ROOT / "data" / "run.log"


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def publish(trade_date: str) -> bool:
    """리포트를 GitHub Pages 에 올린다.

    변경이 없으면 커밋하지 않는다 (빈 커밋이 매일 쌓이면 이력이 지저분해진다).
    푸시 실패는 치명적이지 않다 — 카톡 본문은 이미 갔고 링크만 어제 것을 가리킬 뿐이다.
    """
    import subprocess

    def git(*a, timeout=90):
        # Windows 기본 인코딩(cp949)으로 읽으면 한글 커밋 메시지에서 깨진다.
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})

    if git("rev-parse", "--git-dir").returncode != 0:
        log("△ git 저장소가 아닙니다. 리포트 발행을 건너뜁니다.")
        return False

    git("add", "docs")
    if not git("diff", "--cached", "--quiet").returncode:
        log("  리포트 변경 없음 — 발행 생략")
        return True

    res = git("commit", "-m", f"브리핑 {trade_date}")
    if res.returncode != 0:
        log(f"△ 커밋 실패: {res.stderr.strip()[:120]}")
        return False

    res = git("push", "origin", "HEAD", timeout=120)
    if res.returncode != 0:
        log(f"△ 푸시 실패: {res.stderr.strip()[:160]}")
        return False

    log("✓ GitHub Pages 발행 완료")
    return True


def step(name: str, fn, *args, **kwargs):
    """한 단계 실행. 실패해도 예외를 삼키고 계속 간다."""
    try:
        result = fn(*args, **kwargs)
        return result, None
    except Exception as exc:                              # noqa: BLE001
        log(f"✗ {name} 실패: {type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)
        return None, f"{name}({type(exc).__name__})"


def main() -> int:
    ap = argparse.ArgumentParser(description="매일 경제 브리핑")
    ap.add_argument("--no-send", action="store_true", help="카카오 발송 생략")
    ap.add_argument("--no-fetch", action="store_true", help="수집 생략")
    ap.add_argument("--no-publish", action="store_true", help="GitHub 발행 생략")
    args = ap.parse_args()

    log("=" * 52)
    log("브리핑 생성 시작")
    failures: list[str] = []

    db.init()

    if not args.no_fetch:
        r, err = step("시세 수집", market.collect)
        if err:
            failures.append(err)
        elif r:
            log(f"✓ 시세: {len(r.ok)}건 / {r.rows_written} rows")
            if r.failed:
                failures.append(f"시세일부({','.join(r.failed)})")

        r, err = step("거시지표 수집", macro.collect)
        if err:
            failures.append(err)
        elif r:
            log(f"✓ 거시: {len(r.ok)}건 / {r.rows} rows")
            if r.failed:
                failures.append(f"거시일부({','.join(r.failed)})")

        r, err = step("수급 수집", flows.collect)
        if err:
            failures.append(err)
        elif r:
            if r.failed:
                log(f"△ 수급: {r.summary()}")
                failures.append(f"수급({','.join(r.failed)})")
            else:
                log(f"✓ 수급: {r.rows} rows")

        n, err = step("파생지표 계산", metrics.run)
        if err:
            failures.append(err)
        else:
            log(f"✓ 지표 계산: {n} rows")

    result, err = step("리포트 생성", report.render)
    if err or result is None:
        log("✗ 리포트를 만들지 못했습니다. 중단합니다.")
        return 1

    path, payload = result
    log(f"✓ 리포트: {path} ({path.stat().st_size:,} bytes)")

    card = payload["score"]
    if card.hit + card.miss:
        log(f"✓ 채점: {card.hit}/{card.hit + card.miss} 적중")
    log(f"  결론: {payload['verdict']['headline']}")

    if not args.no_publish:
        step("리포트 발행", publish, payload["trade_date"])

    if args.no_send:
        log("발송 생략 (--no-send)")
        log(f"브라우저로 확인: file:///{path.as_posix()}")
        return 0

    messages = kakao_text.build(
        verdict=payload["verdict"], score=card, track=payload["track"],
        notable_rows=payload["notable_rows"], flow_stats=payload["flow_stats"],
        triggers=payload["triggers"], date_kr=payload["date_kr"])

    env = macro._load_env()
    link = env.get("REPORT_BASE_URL", "").strip() or None

    from brief.deliver import kakao                       # noqa: PLC0415
    try:
        sent = kakao.send_sequence(messages, link_url=link)
        log(f"✓ 카카오톡 {sent}건 발송")
    except Exception as exc:                              # noqa: BLE001
        log(f"✗ 카카오 발송 실패: {exc}")
        failures.append("카카오발송")

    if failures:
        log("⚠ 일부 단계 실패: " + ", ".join(failures))
    log("완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
