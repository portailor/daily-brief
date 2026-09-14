"""확률로 본 투자 참고 — 5거래일 예상 범위와 위험 기반 가이드.

왜 방향이 아니라 범위인가
  이동평균·연속일·VIX 같은 조건으로 '5거래일 뒤 오른다/내린다'를 판단하는 규칙을
  10년치 데이터로 워크포워드 검증했더니, 방향을 말한 날의 적중률이 48~50%로
  같은 날 무조건 '상승'이라고 한 것보다 낮았다. 방향 조언은 체계적으로 틀린다.
  반면 '얼마나 흔들릴지'는 맞힐 수 있었다. 80% 범위라고 한 것이 실제로
  78.7~81.7% 맞았다. 그래서 이 파일은 맞힐 수 있는 것만 말한다.

계산 (전부 그날 이전에 확정된 데이터만 사용)
  1. 최근 60거래일 일간 변동률의 표준편차 → 5거래일 규모로 환산 (×√5)
  2. 과거 5거래일 수익률을 그 규모로 나눠 표준화한 값들의 10%·90% 분위수
  3. 오늘의 규모 × 분위수 = 오늘의 80% 예상 범위
  정규분포를 가정하지 않고 과거 실제 분포를 쓰므로, 급락이 잦은 시장의
  두꺼운 꼬리가 반영된다.

행동 가이드는 방향이 아니라 '위험의 크기'에서만 나온다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from brief.clock import label                     # noqa: E402

HORIZON = 5
VOL_WINDOW = 60
MIN_HISTORY = 500
LOW_Q, HIGH_Q = 0.10, 0.90          # 80% 범위

# 보유기간별 과거 성적 — '언제 사나'가 아니라 '사면 얼마나 들고 있어야 하나'
HOLDING = [(5, "1주"), (20, "1달"), (60, "3달"), (250, "1년")]

# id: (이름, 단위 종류)
TARGETS = {
    "KOSPI":  ("코스피", "index"),
    "KOSDAQ": ("코스닥", "index"),
    "SPX":    ("S&P 500", "index"),
    "NASDAQ": ("나스닥", "index"),
    "USDKRW": ("원/달러 환율", "fx"),
}


@dataclass
class RangeView:
    id: str
    name: str
    kind: str
    asof: str
    close: float
    lo_pct: float            # 5거래일 80% 범위 하단 (%)
    hi_pct: float
    coverage: float          # 과거 검증: 80% 범위 안에 실제로 들어간 비율
    below_rate: float        # 과거 검증: 하단보다 더 떨어진 비율 (목표 10%)
    checked_days: int        # 검증에 쓴 거래일 수
    vol_rank: float          # 지금 변동성이 과거 전체 중 몇 % 위치인가 (100=가장 높음)
    up_rate: float           # 과거 전체에서 5거래일 뒤 오른 비율
    history_from: str
    dir_acc: float | None = None     # 방향 규칙을 썼다면의 적중률 (참고: 쓰지 않는 이유)
    dir_base: float | None = None
    holding: list | None = None      # [{label, days, up_rate, median, q10, periods}]

    @property
    def price_lo(self) -> float:
        return self.close * (1 + self.lo_pct / 100)

    @property
    def price_hi(self) -> float:
        return self.close * (1 + self.hi_pct / 100)

    @property
    def regime(self) -> str:
        r = self.vol_rank
        if r >= 90:
            return "변동성 매우 높음"
        if r >= 70:
            return "변동성 높음"
        if r >= 30:
            return "변동성 보통"
        return "변동성 낮음"

    @property
    def rank_text(self) -> str:
        if self.vol_rank >= 50:
            return f"과거 {self.history_years}년 중 상위 {max(1, round(100 - self.vol_rank))}%"
        return f"과거 {self.history_years}년 중 하위 {max(1, round(self.vol_rank))}%"

    @property
    def history_years(self) -> int:
        start = pd.Timestamp(self.history_from)
        return max(1, round((pd.Timestamp(self.asof) - start).days / 365.25))

    def guide(self) -> list[str]:
        """위험의 크기에서만 나오는 행동 가이드. 방향 판단은 포함하지 않는다."""
        down = abs(self.lo_pct)
        lines: list[str] = []

        if self.kind == "fx":
            lines.append(
                f"환전이 필요하다면, 5거래일 안에 환율이 {self.price_lo:,.0f}원~"
                f"{self.price_hi:,.0f}원 사이에서 움직일 확률이 약 80%입니다.")
            if self.vol_rank >= 70:
                lines.append("변동이 큰 구간이라, 필요한 금액을 한 번에 바꾸기보다 "
                             "여러 번에 나누면 한 시점의 환율에 모두 걸리는 위험을 줄일 수 있습니다.")
            return lines

        lines.append(
            f"5거래일 안에 {down:.1f}%보다 더 떨어질 확률은 약 10%입니다. "
            f"이 정도 하락을 감당할 수 없는 금액이라면 비중을 줄여 두는 것이 안전합니다.")
        if self.vol_rank >= 70:
            lines.append("변동성이 높은 구간이라 같은 금액이라도 손익 폭이 평소보다 큽니다. "
                         "새로 사거나 팔 계획이 있다면 한 번에 하기보다 나눠서 하는 편이 "
                         "한 시점의 가격에 모두 걸리는 위험을 줄입니다.")
        elif self.vol_rank < 30:
            lines.append("평소보다 조용한 구간입니다. 다만 변동성이 낮은 상태가 "
                         "계속된다는 보장은 없습니다.")
        return lines


def _vol_rank(vol: pd.Series) -> float:
    v = vol.dropna()
    if v.empty:
        return 50.0
    return float((v < v.iloc[-1]).mean() * 100)


def build_range(conn, iid: str, direction_check: bool = True) -> RangeView | None:
    name, kind = TARGETS[iid]
    df = pd.read_sql_query(
        "SELECT trade_date, close FROM observations WHERE instrument=? ORDER BY trade_date",
        conn, params=(iid,))
    if len(df) < MIN_HISTORY + VOL_WINDOW + HORIZON:
        return None
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    close = df.set_index("trade_date")["close"]

    r1 = close.pct_change()
    r5 = close.shift(-HORIZON) / close - 1
    vol = r1.rolling(VOL_WINDOW, min_periods=40).std()
    scale = vol * np.sqrt(HORIZON)
    known = r5.notna() & scale.notna() & (scale > 0)
    z = (r5 / scale).where(known)

    # 워크포워드 분위수: t 시점에는 t−HORIZON 까지 결과가 확정된 z 만 쓴다
    past = z.shift(HORIZON)
    q_lo = past.expanding(min_periods=MIN_HISTORY).quantile(LOW_Q)
    q_hi = past.expanding(min_periods=MIN_HISTORY).quantile(HIGH_Q)

    ok = known & q_lo.notna()
    inside = (z >= q_lo) & (z <= q_hi)
    below = z < q_lo

    last_lo, last_hi, last_scale = q_lo.dropna().iloc[-1], q_hi.dropna().iloc[-1], scale.iloc[-1]

    view = RangeView(
        id=iid, name=name, kind=kind,
        asof=close.index[-1].strftime("%Y-%m-%d"), close=float(close.iloc[-1]),
        lo_pct=float(last_lo * last_scale * 100), hi_pct=float(last_hi * last_scale * 100),
        coverage=float(inside[ok].mean()), below_rate=float(below[ok].mean()),
        checked_days=int(ok.sum()), vol_rank=_vol_rank(vol),
        up_rate=float((r5[r5.notna()] > 0).mean()),
        history_from=close.index[0].strftime("%Y-%m-%d"),
    )

    if kind == "index":
        rows = []
        for h, lab in HOLDING:
            rh = (close.shift(-h) / close - 1).dropna() * 100
            if len(rh) < h * 3:
                continue
            rows.append({
                "label": lab, "days": h,
                "up_rate": float((rh > 0).mean()),
                "median": float(rh.median()),
                "q10": float(rh.quantile(0.10)),
                # 서로 겹치지 않는 구간 수 — 확률이 얼마나 튼튼한지 가늠하는 값
                "periods": int(len(close) // h),
            })
        view.holding = rows

    if direction_check:
        # 방향 규칙을 쓰지 않는 근거를 매일 다시 잰다 (가장 자주 말하는 설정 기준)
        try:
            from brief.interpret import views as V
            name_, _, _, korean = V.TARGETS[iid]
            vdf = V._load(conn, iid)
            vdf["vix_state"] = V._vix(conn, vdf.index, korean)
            fwd = vdf["close"].shift(-V.HORIZON) / vdf["close"] - 1
            kn = fwd.notna()
            y = (fwd > 0).astype(float).where(kn)
            old = (V.Z, V.OVERLAP)
            V.Z, V.OVERLAP = 1.28, 1
            try:
                st = V._stance_series(vdf, y, kn)
            finally:
                V.Z, V.OVERLAP = old
            said = (st != 0) & kn
            if said.sum():
                right = ((st > 0) & (y == 1.0)) | ((st < 0) & (y == 0.0))
                view.dir_acc = float(right[said].mean())
                view.dir_base = float(y[said].mean())
        except Exception:                               # noqa: BLE001
            pass

    return view


def build_all(conn) -> list[RangeView]:
    out = []
    for iid in TARGETS:
        try:
            v = build_range(conn, iid)
        except Exception:                               # noqa: BLE001
            v = None
        if v:
            out.append(v)
    return out


if __name__ == "__main__":
    from brief import db
    with db.connect() as c:
        for v in build_all(c):
            unit = "원" if v.kind == "fx" else ""
            print(f"\n■ {v.name} ({label(v.asof)} 종가 {v.close:,.2f}{unit}) — {v.regime} ({v.rank_text})")
            print(f"   5거래일 80% 범위: {v.lo_pct:+.2f}% ~ {v.hi_pct:+.2f}%  "
                  f"({v.price_lo:,.2f} ~ {v.price_hi:,.2f})")
            print(f"   과거 검증 {v.checked_days}일: 범위 안 {v.coverage:.1%} (목표 80%), "
                  f"하단 아래 {v.below_rate:.1%} (목표 10%)")
            print(f"   참고: 5거래일 뒤 오른 비율 {v.up_rate:.0%}")
            if v.dir_acc is not None:
                print(f"   방향 규칙을 썼다면 적중 {v.dir_acc:.1%} vs 같은 날 무조건 상승 {v.dir_base:.1%}")
            for g in v.guide():
                print(f"   → {g}")
            for h in v.holding or []:
                print(f"   보유 {h['label']}: 이익 {h['up_rate']:.0%} · 중앙 {h['median']:+.1f}% · "
                      f"나쁜 10% {h['q10']:+.1f}% · 독립 구간 약 {h['periods']}개")
