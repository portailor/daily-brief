"""매일 아침 실행되는 진입점.

  python run.py                전체: 수집 → 계산 → 채점 → 리포트 → 발행 → 발송
  python run.py --no-send      발송 없이 리포트까지만
  python run.py --send-only    만들어 둔 메시지(outbox)만 발송
  python run.py --no-fetch     수집 없이 기존 데이터로
  python run.py --no-publish   웹 발행 없이
  python run.py --weekly       요일과 상관없이 주간 정리로
  python run.py --force        일요일에도 실행

요일별 동작 (한국시간)
  화~토  일일 브리핑 — 전날 한국·미국 마감 반영
  일     쉼 — 새로 마감된 거래가 없다
  월     주간 정리 + 이번 주 일정 — 일요일과 데이터가 같으므로 한 주를 묶어 본다

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
from brief.collect import results as results_mod  # noqa: E402
from brief.analyze import metrics                       # noqa: E402
from brief.collect import detail, flows, macro, market  # noqa: E402
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


def sync_before_publish() -> bool:
    """발행할 실행이라면, 파일을 새로 만들기 '전에' 원격을 받아 둔다.

    클라우드가 매일 아침 docs/ 와 기록 파일을 커밋한다. 로컬에서 먼저 파일을
    다시 만든 뒤에 받으면 같은 파일끼리 충돌한다(2026-09-15 실제로 발생).
    """
    import subprocess
    run_git = lambda *a: subprocess.run(                      # noqa: E731
        ["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})

    if run_git("rev-parse", "--git-dir").returncode != 0:
        return True
    tracked = ["docs", "data/predictions.csv", "data/state.json"]
    if run_git("status", "--porcelain", "--", *tracked).stdout.strip():
        # 이전 로컬 실행이 남긴 결과물 — 어차피 이번 실행에서 다시 만든다
        run_git("checkout", "--", *tracked)
        run_git("clean", "-fdq", "--", "docs")
    res = run_git("pull", "--rebase", "-q", "origin", "main")
    if res.returncode != 0:
        run_git("rebase", "--abort")
        log(f"△ 원격 변경을 받지 못했습니다: {res.stderr.strip()[:160]}")
        return False
    return True


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

    # 리베이스·병합이 멈춰 있는 상태에서 커밋하면, 스테이징된 다른 변경까지
    # 엉뚱한 메시지로 함께 커밋된다(2026-09-15 실제로 발생). 그런 때는 발행하지 않는다.
    git_dir = ROOT / git("rev-parse", "--git-dir").stdout.strip()
    if any((git_dir / d).exists() for d in ("rebase-merge", "rebase-apply", "MERGE_HEAD")):
        log("△ git 리베이스/병합이 진행 중이라 발행을 건너뜁니다.")
        return False
    if git("symbolic-ref", "-q", "HEAD").returncode != 0:
        log("△ 브랜치에 있지 않아(detached HEAD) 발행을 건너뜁니다.")
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


def wait_until_live(url: str, build_id: str, timeout_s: int = 300) -> bool:
    """방금 만든 페이지가 웹에 반영될 때까지 기다린다. GitHub Pages 배포는 보통 30~90초.

    응답 코드 200 만 보면 안 된다. 같은 날짜 페이지가 이미 올라가 있으면
    옛 내용으로도 200 이 나온다. 페이지에 심어 둔 빌드 번호가 맞는지 본다.
    """
    import requests

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            # 쿼리를 붙여 CDN 캐시를 우회한다
            res = requests.get(f"{url}?check={int(time.time())}", timeout=10)
            if res.status_code == 200 and build_id in res.text:
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
    today = clock.today_kst()

    if today.weekday() == 6 and not (args.force or args.weekly):
        log("일요일 — 새로 마감된 거래가 없어 쉬는 날입니다")
        _write_json(OUTBOX, {"skip": True, "brief_date": today.isoformat()})
        return 0

    mode = "weekly" if (args.weekly or today.weekday() == 0) else "daily"
    log(f"브리핑 생성 시작 ({'주간 정리' if mode == 'weekly' else '일일'})")
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

        n, err = step("한국 지수(KRX 공식)", flows.collect_indices)
        if err:
            failures.append(err)
        elif n:
            log(f"✓ 한국 지수 KRX 공식값: {n} rows")

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

        d, err = step("종목·업종 상세", detail.collect)
        if err:
            failures.append(err)
        elif d:
            probs = [x for x in (d.get("kr", {}).get("error"), d.get("us", {}).get("error")) if x]
            probs += d.get("kr", {}).get("errors", [])
            if probs:
                log(f"△ 상세 일부 실패: {', '.join(probs)}")
                failures.append("상세일부")
            else:
                log(f"✓ 종목·업종 상세: 국내 {d.get('kr', {}).get('date')} · 미국 {d.get('us', {}).get('date')}")

    result, err = step("리포트 생성", report.render, mode=mode)
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
    is_new = (payload["market_key"] != state.get("market_key")
              or state.get("brief_date") == today.isoformat())

    if mode == "weekly":
        # 주간 정리는 새 거래가 없어도 보낸다 — 원래 그런 날을 위한 메시지다
        is_new = True
        message = kakao_text.build_weekly(payload)
        _write_json(STATE, results_mod.mark_shown(
            {**state, "market_key": payload["market_key"], "brief_date": today.isoformat()},
            payload.get("results", []), today))
        log(f"  주간 정리: {payload['week'].period if payload['week'] else '-'}")
    elif is_new:
        message = kakao_text.build(payload)
        _write_json(STATE, results_mod.mark_shown(
            {**state, "market_key": payload["market_key"], "brief_date": today.isoformat()},
            payload.get("results", []), today))
        log(f"  결론: {payload['verdict']['headline']}")
    else:
        message = kakao_text.build_no_new_data(payload, today.weekday())
        log(f"  새 거래 데이터 없음 (지난 브리핑 {state.get('brief_date')}) — 안내만 보냅니다")

    base = macro._load_env().get("REPORT_BASE_URL", "").strip()
    link = (base.rstrip("/") + "/" + payload["page_name"]) if base else None

    _write_json(OUTBOX, {"message": message, "link": link,
                         "build_id": payload["build_id"],
                         "brief_date": today.isoformat(), "is_new": is_new,
                         # 메일은 200자 제한이 없어 표와 상세도 함께 보낸다
                         "dashboard": payload.get("dashboard", []),
                         "detail": payload.get("detail", {})})
    log(f"✓ 발송 대기 메시지 준비 ({len(message)}자)")

    if failures:
        log("⚠ 일부 단계 실패: " + ", ".join(failures))
    return 0


# ─────────────────────────────────────────────────────────────
#  발송
# ─────────────────────────────────────────────────────────────

def send() -> int:
    box = _read_json(OUTBOX)
    if box.get("skip"):
        log("오늘은 발송하지 않는 날입니다")
        return 0
    if not box.get("message"):
        log("✗ 보낼 메시지가 없습니다. 먼저 생성 단계를 실행하세요.")
        return 1

    link = box.get("link")
    if link:
        if wait_until_live(link, box.get("build_id", "")):
            log(f"✓ 새 리포트 반영 확인: {link}")
        else:
            # 반영이 늦어도 본문은 보낸다. 몇 분 뒤면 새 내용으로 열린다.
            log(f"△ 새 리포트가 아직 반영되지 않았지만 발송합니다: {link}")
        # 폰 브라우저가 예전에 연 페이지를 캐시해 둔 경우를 피한다
        link = f"{link}?v={box.get('build_id', '')}"

    from brief.deliver import kakao                       # noqa: PLC0415
    try:
        kakao.send_text(box["message"], link_url=link, button_title="전체 브리핑 보기")
        log("✓ 카카오톡 1건 발송 (나에게)")
        # 오늘 보냈다는 기록. 예약 실행을 두 번 걸어 두었기 때문에(정시 보장이 안 돼서)
        # 앞의 실행이 이미 보냈으면 뒤의 실행은 이 값을 보고 아무것도 하지 않는다.
        _write_json(STATE, {**_read_json(STATE), "sent_date": box.get("brief_date", "")})
    except Exception as exc:                              # noqa: BLE001
        log(f"✗ 카카오 발송 실패: {exc}")
        return 1

    # 친구 발송은 본인 발송과 분리한다. 친구 쪽이 막혀도 내 브리핑은 이미 갔고,
    # 실패를 이유로 전체를 실패로 만들면 매일 아침 워크플로가 빨갛게 뜬다.
    # 이메일 — 카카오톡 친구 발송은 받는 사람이 카카오디벨로퍼스 계정을 만들고
    # 팀원 초대를 수락해야만 가능해서, 함께 받을 사람에게는 메일로 보낸다.
    from brief.deliver import mailer                      # noqa: PLC0415
    if mailer.recipients():
        sent, problem = mailer.send(box["message"], link=link,
                                    payload={"dashboard": box.get("dashboard", []),
                                             "detail": box.get("detail", {})},
                                    subject=f"경제 브리핑 {box.get('brief_date', '')}")
        if sent:
            log(f"✓ 이메일 발송: {', '.join(sent)}")
        if problem:
            log(f"△ 이메일 발송 실패 — {problem}")

    from brief.deliver import friends                     # noqa: PLC0415
    if friends.load_recipients():
        try:
            sent, problems = friends.send_to_friends(
                box["message"], link_url=link, button_title="전체 브리핑 보기")
            if sent:
                log(f"✓ 카카오톡 {len(sent)}건 발송 (친구: {', '.join(sent)})")
            for why in problems:
                log(f"△ 친구 발송 실패 — {why}")
        except Exception as exc:                          # noqa: BLE001
            log(f"△ 친구 발송 건너뜀: {exc}")

    log("완료")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="매일 경제 브리핑")
    ap.add_argument("--no-send", action="store_true", help="카카오 발송 생략")
    ap.add_argument("--send-only", action="store_true", help="만들어 둔 메시지만 발송")
    ap.add_argument("--no-fetch", action="store_true", help="수집 생략")
    ap.add_argument("--no-publish", action="store_true", help="GitHub 발행 생략")
    ap.add_argument("--weekly", action="store_true", help="요일과 상관없이 주간 정리")
    ap.add_argument("--force", action="store_true", help="일요일에도 실행")
    args = ap.parse_args()

    if args.send_only:
        return send()

    if not args.no_publish and not sync_before_publish():
        log("원격과 맞추지 못해 이번 실행은 발행하지 않습니다.")
        args.no_publish = True

    code = generate(args)
    if code != 0:
        return code
    if _read_json(OUTBOX).get("skip"):
        return 0

    if not args.no_publish:
        step("리포트 발행", publish, f"브리핑 {clock.today_kst().isoformat()}")

    if args.no_send:
        log("발송 생략 (--no-send)")
        return 0

    return send()


if __name__ == "__main__":
    raise SystemExit(main())
