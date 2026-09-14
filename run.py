"""매일 아침 실행되는 진입점.

  python run.py                전체: 수집 → 계산 → 채점 → 리포트 → 발행 → 발송
  python run.py --no-send      발송 없이 리포트까지만
  python run.py --send-only    만들어 둔 메시지(outbox)만 발송
  python run.py --no-fetch     수집 없이 기존 데이터로
  python run.py --no-publish   웹 발행 없이

발송은 반드시 리포트가 웹에 올라간 뒤에 한다.
예전에는 카톡이 먼저 도착하고 리포트는 그 뒤에 올라가서, 곧바로 링크를
누르면 전날 페이지가 보였다. 그래서 생성 단계는 보낼 메시지를 outbox 에
남겨두고, 발송 단계는 링크가 실제로 열리는지 확인한 다음에 보낸다.

설계 원칙: 한 단계가 실패해도 나머지는 진행한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from brief import clock, db                             # noqa: E402
from brief.analyze import metrics                       # noqa: E402
from brief.collect import flows, macro, market          # noqa: E402
from brief.render import kakao_text, report             # noqa: E402

LOG_PATH = ROOT / "data" / "run.log"
OUTBOX = ROOT / "data" / "outbox.json"
STATE = ROOT / "data" / "state.json"      # 커밋된다 — 마지막으로 보낸 거래일 기록


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def step(name: str, fn, *args, **kwargs):
    """한 단계 실행. 실패해도 예외를 삼키고 계속 간다."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:                              # noqa: BLE001
        log(f"✗ {name} 실패: {type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)
        return None, f"{name}({type(exc).__name__})"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def publish(message: str) -> bool:
    """리포트를 GitHub Pages 에 올린다 (로컬 실행용 — 클라우드는 워크플로우가 커밋한다)."""
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

    git("add", "docs", "data/predictions.csv", "data/state.json")
    if not git("diff", "--cached", "--quiet").returncode:
        log("  리포트 변경 없음 — 발행 생략")
        return True

    res = git("commit", "-m", message)
    if res.returncode != 0:
        log(f"△ 커밋 실패: {res.stderr.strip()[:120]}")
        return False

    res = git("push", "origin", "HEAD", timeout=120)
    if res.returncode != 0:
        log(f"△ 푸시 실패: {res.stderr.strip()[:160]}")
        return False

    log("✓ GitHub Pages 발행 완료")
    return True


def wait_until_live(url: str, timeout_s: int = 240) -> bool:
    """링크가 실제로 열릴 때까지 기다린다. GitHub Pages 배포는 보통 30~90초."""
    import requests

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=10).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(10)
    return False


# ─────────────────────────────────────────────────────────────
#  생성
# ─────────────────────────────────────────────────────────────

def generate(args) -> int:
    log("=" * 52)
    log("브리핑 생성 시작")
    failures: list[str] = []

    db.init()

    # 클라우드에서는 매 실행이 빈 DB로 시작한다. 예측 기록만 되살린다.
    with db.session() as conn:
        restored = db.import_predictions(conn)
    if restored:
        log(f"✓ 예측 기록 {restored}건 복원")

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

        with db.session() as conn:
            removed = db.purge_unfinished(conn)
        if removed:
            log(f"  미마감·주말 행 {removed}건 제거")

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
    log(f"✓ 리포트: {path.parent / payload['page_name']}")
    log(f"  {payload['basis']}")

    card = payload["score"]
    if card.hit + card.miss:
        log(f"✓ 채점: {card.hit}/{card.hit + card.miss} 적중")

    with db.session() as conn:
        saved = db.export_predictions(conn)
    log(f"✓ 예측 기록 {saved}건 저장")

    # ── 새 거래일 데이터가 있는가 ───────────────────────────
    # 한국·미국 대표 지수의 기준일이 지난번 브리핑과 같으면 주말·휴장이다.
    # 같은 날 다시 돌린 경우(수동 재실행)는 새 브리핑으로 취급한다.
    state = _read_json(STATE)
    today = clock.today_kst()
    is_new = (payload["market_key"] != state.get("market_key")
              or state.get("brief_date") == today.isoformat())

    if is_new:
        message = kakao_text.build(payload)
        _write_json(STATE, {"market_key": payload["market_key"],
                            "brief_date": today.isoformat()})
        log(f"  결론: {payload['verdict']['headline']}")
    else:
        message = kakao_text.build_no_new_data(payload, today.weekday())
        log(f"  새 거래 데이터 없음 (지난 브리핑 {state.get('brief_date')}) — 안내만 보냅니다")

    base = macro._load_env().get("REPORT_BASE_URL", "").strip()
    link = (base.rstrip("/") + "/" + payload["page_name"]) if base else None

    _write_json(OUTBOX, {"message": message, "link": link,
                         "brief_date": today.isoformat(), "is_new": is_new})
    log(f"✓ 발송 대기 메시지 준비 ({len(message)}자)")

    if failures:
        log("⚠ 일부 단계 실패: " + ", ".join(failures))
    return 0


# ─────────────────────────────────────────────────────────────
#  발송
# ─────────────────────────────────────────────────────────────

def send() -> int:
    box = _read_json(OUTBOX)
    if not box.get("message"):
        log("✗ 보낼 메시지가 없습니다. 먼저 생성 단계를 실행하세요.")
        return 1

    link = box.get("link")
    if link:
        if wait_until_live(link):
            log(f"✓ 링크 확인: {link}")
        else:
            # 링크가 늦어도 본문은 보낸다. 몇 분 뒤면 열린다.
            log(f"△ 링크가 아직 열리지 않지만 발송합니다: {link}")

    from brief.deliver import kakao                       # noqa: PLC0415
    try:
        kakao.send_text(box["message"], link_url=link, button_title="전체 브리핑 보기")
        log("✓ 카카오톡 1건 발송")
    except Exception as exc:                              # noqa: BLE001
        log(f"✗ 카카오 발송 실패: {exc}")
        return 1

    log("완료")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="매일 경제 브리핑")
    ap.add_argument("--no-send", action="store_true", help="카카오 발송 생략")
    ap.add_argument("--send-only", action="store_true", help="만들어 둔 메시지만 발송")
    ap.add_argument("--no-fetch", action="store_true", help="수집 생략")
    ap.add_argument("--no-publish", action="store_true", help="GitHub 발행 생략")
    args = ap.parse_args()

    if args.send_only:
        return send()

    code = generate(args)
    if code != 0:
        return code

    if not args.no_publish:
        step("리포트 발행", publish, f"브리핑 {clock.today_kst().isoformat()}")

    if args.no_send:
        log("발송 생략 (--no-send)")
        return 0

    return send()


if __name__ == "__main__":
    raise SystemExit(main())
