"""서울 신고가 기록을 처음 한 번 채운다 — 2006년 1월부터 지지난달까지, 25개 구.

  python scripts/backfill_highs.py

끊겨도 다시 실행하면 이어서 채운다(달마다 저장). 다 채우면 complete 를 켠다 —
매일 실행은 complete 가 켜진 기록으로만 신고가를 판정한다.
"""
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from brief.clock import today_kst                          # noqa: E402
from brief.collect import housing, records                  # noqa: E402
from brief.collect.macro import _load_env                   # noqa: E402

key = _load_env()["DATA_GO_KR_KEY"]
fetch = lambda c, ym: housing._trades(key, c, ym)            # noqa: E731
log = lambda s: print(s, flush=True)                         # noqa: E731

today = today_kst()
last_m = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
upto = (last_m - timedelta(days=1)).replace(day=1)
store = records.load()
t = time.time()

# 1) 뒤쪽 — 기록 시작 이후 ~ 지지난달
records.fold(store, fetch, housing.SEOUL_GU, upto, log=log, workers=5)
# 2) 앞쪽 — 2006년 1월까지
records.fill_back(store, fetch, housing.SEOUL_GU, records.HISTORY_START, log=log)

store["complete"] = (store["through"] == upto.strftime("%Y-%m")
                     and store["since"] == records.HISTORY_START.strftime("%Y-%m"))
records.save(store)
print(f"완료: {store['since']} ~ {store['through']} · 종류 {len(store['max']):,}개 · "
      f"complete={store['complete']} · {time.time() - t:.0f}초 · "
      f"{records.STORE.stat().st_size / 1e6:.1f}MB", flush=True)
