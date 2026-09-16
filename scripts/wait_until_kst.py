"""한국시간 HH:MM 까지 기다린다. 이미 지났으면 바로 끝낸다.

워크플로의 대기 단계에서 쓴다. date -d "today 06:15" 는 러너의 시간대 데이터에
따라 결과가 달라질 수 있어, 한국은 서머타임이 없다는 점을 이용해 UTC+9 로
직접 계산한다.

  python scripts/wait_until_kst.py 06:15
"""
import sys
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

hh, mm = map(int, sys.argv[1].split(":"))
now = datetime.now(KST)
target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
wait = (target - now).total_seconds()

if "--dry-run" in sys.argv:
    print(f"지금 {now:%m/%d %H:%M} KST → 목표 {target:%H:%M} · {wait / 60:+.0f}분")
elif wait <= 0:
    print(f"한국시간 {now:%H:%M} — {hh:02d}:{mm:02d} 이미 지남, 바로 진행")
else:
    print(f"한국시간 {now:%H:%M} — {hh:02d}:{mm:02d} 까지 {wait / 60:.0f}분 대기")
    sys.stdout.flush()
    time.sleep(wait)
    print(f"대기 끝: {datetime.now(KST):%H:%M}")
