"""일시적 실패에 대한 재시도.

이 시스템은 하루에 한 번만 돈다. 그 한 번에 네트워크가 잠깐 흔들리면
그날 브리핑이 통째로 없어진다. 실제로 첫 스케줄러 실행에서
카카오가 ConnectionResetError 로 끊겼다.

되돌릴 수 없는 일(중복 발송 등)을 만들지 않도록,
'보내기 전에 끊긴 경우'에 해당하는 연결 계열 오류만 재시도한다.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

import requests

T = TypeVar("T")

# 재시도해도 안전한 오류들. 서버가 이미 요청을 받아 처리했을 수 있는
# 상황(예: 200 이외의 응답 코드)은 여기 넣지 않는다.
TRANSIENT = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    ConnectionResetError,
)


def with_retry(fn: Callable[[], T], *,
               attempts: int = 3,
               base_delay: float = 2.0,
               label: str = "",
               on_retry: Callable[[int, Exception], None] | None = None) -> T:
    """fn 을 최대 attempts 번 시도한다. 대기 시간은 2초 → 4초 → 8초.

    마지막 시도까지 실패하면 원래 예외를 그대로 올린다 —
    호출한 쪽이 무엇이 실패했는지 알아야 하기 때문이다.
    """
    last: Exception | None = None

    for i in range(1, attempts + 1):
        try:
            return fn()
        except TRANSIENT as exc:
            last = exc
            if i == attempts:
                break
            delay = base_delay * (2 ** (i - 1))
            if on_retry:
                on_retry(i, exc)
            else:
                tag = f"[{label}] " if label else ""
                print(f"  {tag}일시적 오류 ({type(exc).__name__}) — "
                      f"{delay:.0f}초 뒤 재시도 {i}/{attempts - 1}")
            time.sleep(delay)

    assert last is not None
    raise last
