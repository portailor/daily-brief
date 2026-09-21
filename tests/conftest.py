"""테스트 공용 설정.

이 저장소의 테스트는 네트워크를 쓰지 않는다. yfinance·FRED·ECOS·KRX·카카오는
전부 바깥 세상이라 CI 에서 느리고, 무엇보다 시장이 움직이면 결과가 바뀐다.
채점·계산·날짜 판정처럼 '입력이 같으면 답이 같아야 하는' 부분만 검사한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
