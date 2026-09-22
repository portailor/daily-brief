#!/usr/bin/env bash
# 방금 만든 커밋을 origin/main 위로 다시 올린다.
#
# 브리핑 실행은 발송 시각까지 몇 시간을 기다린다. 그 사이 다른 커밋이 올라오면
# push 가 거절된다 (9/17 아침, 실행 도중 코드를 올려 발송이 멈췄다).
# 그때 넣은 `git pull --rebase -X theirs` 는 확실히 멈추지는 않게 해 줬지만
# 충돌 파일을 가리지 않는다. docs/ 는 매번 새로 만드니 한쪽을 골라도 되지만
# data/predictions.csv 는 다르다 — 한쪽을 통째로 고르면 반대쪽에서 찍힌 O/X 가
# 경고 없이 사라진다. 적중률을 담보하는 파일에서 기록이 조용히 없어지는 것은
# 이 시스템에서 가장 나쁜 실패다. 그래서 파일마다 나눠서 푼다.
set -euo pipefail

git fetch -q origin main
git rebase -q origin/main && exit 0

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# rebase 중에는 ours/theirs 가 뒤집힌다:
#   :2 = ours   = origin/main (이미 올라가 있는 판)
#   :3 = theirs = 지금 다시 올리는 중인 이 실행의 커밋
for _ in $(seq 10); do
  conflicts=$(git diff --name-only --diff-filter=U)
  [ -n "$conflicts" ] || break

  for f in $conflicts; do
    case "$f" in
      docs/*|data/state.json|data/seoul_highs.json)
        # 매 실행이 새로 만드는 값이다. 방금 만든 쪽이 최신이다.
        git checkout --theirs -- "$f"
        ;;
      data/predictions.csv)
        git show ":2:$f" > "$tmp/remote.csv"
        git show ":3:$f" > "$tmp/local.csv"
        python3 scripts/merge_predictions.py "$tmp/remote.csv" "$tmp/local.csv" "$f"
        ;;
      *)
        # 코드나 설정이 충돌했다면 기계가 고를 일이 아니다.
        echo "::error::예상치 못한 충돌: $f — 사람이 봐야 합니다"
        git rebase --abort
        exit 1
        ;;
    esac
    git add "$f"
  done

  GIT_EDITOR=true git rebase --continue || true
done

if [ -n "$(git diff --name-only --diff-filter=U)" ]; then
  echo "::error::충돌을 풀지 못했습니다"
  git rebase --abort
  exit 1
fi
