"""파생 지표 계산.

"WTI가 1.4% 내렸다"가 큰 움직임인지 노이즈인지를 정하는 층이다.
여기가 틀리면 '오늘 볼 것' 선정과 예측 트리거가 통째로 틀린다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from brief.analyze import metrics
from brief.analyze.metrics import _streak
from brief.collect.market import Instrument


CFG = {"sigma_window": 60, "range_window": 252, "ma_windows": [20, 60, 200]}

PRICE = Instrument(id="KOSPI", name="코스피", kind="price", group="kr_equity", ticker="^KS11")
RATE = Instrument(id="US10Y", name="미 국채 10년물", kind="rate", group="rates", ticker="^TNX")


def frame(closes, start="2025-01-01"):
    dates = pd.bdate_range(start, periods=len(closes)).strftime("%Y-%m-%d")
    return pd.DataFrame({"trade_date": dates, "close": list(map(float, closes))})


# ── 연속일 ──────────────────────────────────────────────────
def test_연속_상승은_양수로_누적된다():
    got = _streak(pd.Series([np.nan, 1.0, 2.0, 3.0]))
    assert list(got) == [0, 1, 2, 3]


def test_연속_하락은_음수로_누적된다():
    got = _streak(pd.Series([np.nan, -1.0, -2.0, -3.0]))
    assert list(got) == [0, -1, -2, -3]


def test_방향이_바뀌면_연속일이_1부터_다시_센다():
    got = _streak(pd.Series([1.0, 1.0, -1.0, -1.0, 1.0]))
    assert list(got) == [1, 2, -1, -2, 1]


def test_보합은_연속을_끊는다():
    """0 은 방향이 없다. 상승 사이에 끼면 이어 세지 않는다."""
    got = _streak(pd.Series([1.0, 0.0, 1.0]))
    assert list(got) == [1, 0, 1]


# ── 가격 vs 금리 ────────────────────────────────────────────
def test_가격은_퍼센트로_금리는_bp로_잰다():
    """4.83% → 4.94% 는 +2.3% 가 아니라 +11bp 다. 시장은 후자로 말한다."""
    price = metrics.compute(frame([100.0, 110.0]), PRICE, CFG)
    assert price["chg_pct"].iloc[1] == pytest.approx(10.0)
    assert np.isnan(price["chg_bp"].iloc[1])

    rate = metrics.compute(frame([4.83, 4.94]), RATE, CFG)
    assert rate["chg_bp"].iloc[1] == pytest.approx(11.0)
    assert np.isnan(rate["chg_pct"].iloc[1])


def test_첫날은_전일이_없어_변화가_비어_있다():
    out = metrics.compute(frame([100.0, 101.0]), PRICE, CFG)
    assert np.isnan(out["chg"].iloc[0])
    assert out["chg"].iloc[1] == pytest.approx(1.0)


# ── σ ───────────────────────────────────────────────────────
def test_시그마는_오늘_값을_제외한_과거_변동성으로_잰다():
    """오늘 움직임이 자기 분모에 섞이면 큰 움직임일수록 σ가 작아진다."""
    closes = [100.0]
    for _ in range(80):
        closes.append(closes[-1] * 1.01 if len(closes) % 2 else closes[-1] * 0.99)
    closes.append(closes[-1] * 1.20)          # 마지막 날만 +20%

    out = metrics.compute(frame(closes), PRICE, CFG)
    assert out["sigma"].iloc[-1] > 5          # 평소 ±1% 대비 압도적


def test_표본이_모자라면_시그마를_내놓지_않는다():
    """min_periods=20. 모르는 구간을 0 으로 채우면 조용한 날이 이례적으로 보인다."""
    out = metrics.compute(frame([100.0 + i for i in range(10)]), PRICE, CFG)
    assert out["sigma"].isna().all()


def test_변동이_전혀_없으면_시그마는_비어_있다():
    """표준편차 0 으로 나누면 inf 가 된다. 그대로 두면 '무한히 이례적'이 된다."""
    out = metrics.compute(frame([100.0] * 80), PRICE, CFG)
    assert not np.isinf(out["sigma"].to_numpy(dtype=float)).any()


# ── 52주 밴드 ───────────────────────────────────────────────
def test_52주_밴드_위치는_0에서_100_사이다():
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0, 1, 300))
    out = metrics.compute(frame(closes), PRICE, CFG)
    p = out["pct_52w"].dropna()
    assert len(p) > 0
    assert p.min() >= 0 and p.max() <= 100


def test_신고가면_밴드_위치가_100이다():
    closes = [100.0 + i for i in range(300)]        # 매일 신고가
    out = metrics.compute(frame(closes), PRICE, CFG)
    assert out["pct_52w"].iloc[-1] == pytest.approx(100.0)


def test_신저가면_밴드_위치가_0이다():
    closes = [400.0 - i for i in range(300)]
    out = metrics.compute(frame(closes), PRICE, CFG)
    assert out["pct_52w"].iloc[-1] == pytest.approx(0.0)


# ── 이동평균 ────────────────────────────────────────────────
def test_이동평균과_이격도가_맞물린다():
    closes = [100.0] * 30 + [110.0]
    out = metrics.compute(frame(closes), PRICE, CFG)
    last = out.iloc[-1]
    assert last["ma20"] == pytest.approx(np.mean(closes[-20:]))
    assert last["vs_ma20"] == pytest.approx((110.0 / last["ma20"] - 1) * 100)


def test_이동평균을_설정에서_빼도_수집이_멈추지_않는다():
    """metrics 테이블과 리포트는 20·60·200일선을 고정으로 기대한다.

    settings.yaml 에서 하나만 빼도 예전에는 KeyError 로 수집 전체가 멈췄다.
    이제는 '모름'으로 비워 두고 나머지 지표는 그대로 계산한다.
    """
    out = metrics.compute(frame([100.0] * 30), PRICE, {**CFG, "ma_windows": [5, 20]})

    assert out["ma5"].notna().any()          # 설정한 것은 계산된다
    assert out["ma200"].isna().all()         # 빠진 것은 비운다 — 크래시하지 않는다
    assert out["vs_ma200"].isna().all()


# ── 오늘 볼 것 ──────────────────────────────────────────────
class FakeConn:
    def __init__(self, rows): self.rows = rows
    def execute(self, *_a, **_k): return self
    def fetchall(self): return self.rows


def test_임계치를_넘은_지표만_시그마_절댓값_순으로_고른다():
    rows = [
        {"instrument": "A", "sigma": 0.5},     # 평범한 날
        {"instrument": "B", "sigma": -2.4},
        {"instrument": "C", "sigma": 1.8},
    ]
    got = metrics.notable(FakeConn(rows), "2026-03-02", {"sigma_notable": 1.5})
    assert [r["instrument"] for r in got] == ["B", "C"]


def test_아무것도_안_넘으면_빈_목록이다():
    """'오늘은 특별한 게 없다'고 말할 수 있어야 한다."""
    rows = [{"instrument": "A", "sigma": 0.4}, {"instrument": "B", "sigma": -1.2}]
    assert metrics.notable(FakeConn(rows), "2026-03-02", {"sigma_notable": 1.5}) == []
