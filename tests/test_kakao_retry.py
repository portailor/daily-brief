"""카카오 토큰 갱신 — 연결이 안 될 때만 몇 분 간격으로 다시 시도한다 (11/26 아침 kauth 연결 시간 초과)."""
import requests

from brief import retry
from brief.deliver import kakao


class _Res:
    status_code = 200

    def json(self):
        return {"access_token": "AT"}


def _setup(monkeypatch, outcomes):
    calls, waits = [], []
    monkeypatch.setattr(kakao, "_load", lambda: {"rest_api_key": "k", "refresh_token": "r"})
    monkeypatch.setattr(kakao, "_save", lambda d: None)
    monkeypatch.setattr(retry.time, "sleep", waits.append)

    def post(*a, **k):
        calls.append(1)
        o = outcomes[len(calls) - 1]
        if isinstance(o, Exception):
            raise o
        return o
    monkeypatch.setattr(kakao.requests, "post", post)
    return calls, waits


def test_connect_timeout_then_success(monkeypatch):
    calls, waits = _setup(monkeypatch, [requests.exceptions.ConnectTimeout("x"),
                                        requests.exceptions.ConnectionError("y"), _Res()])
    assert kakao.refresh_access_token() == "AT"
    assert len(calls) == 3
    assert waits == [60.0, 120.0]


def test_gives_up_after_four_tries(monkeypatch):
    calls, waits = _setup(monkeypatch, [requests.exceptions.ConnectTimeout("x")] * 4)
    try:
        kakao.refresh_access_token()
        raise AssertionError("예외가 나야 한다")
    except requests.exceptions.ConnectTimeout:
        pass
    assert len(calls) == 4
    assert waits == [60.0, 120.0, 240.0]


def test_read_timeout_not_retried(monkeypatch):
    # 서버가 이미 받아 처리했을 수 있는 응답 대기 시간 초과는 다시 보내지 않는다 (refresh_token 교체가 꼬이지 않게)
    calls, waits = _setup(monkeypatch, [requests.exceptions.ReadTimeout("x"), _Res()])
    try:
        kakao.refresh_access_token()
        raise AssertionError("예외가 나야 한다")
    except requests.exceptions.ReadTimeout:
        pass
    assert len(calls) == 1 and waits == []
