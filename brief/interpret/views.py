"""확률로 본 투자 관점 — 5거래일 뒤 방향.

이 파일이 하는 일은 딱 하나다.
  "지금과 같은 조건이었던 과거 날들에, 5거래일 뒤 오른 비율이
   평소보다 확실히 높았나(낮았나)?"
확실히 다를 때만 매수·매도 쪽 우위를 말하고, 아니면 '관망'이라고 쓴다.

정확성을 지키기 위한 장치
  1. 조건은 미리 정한 6가지뿐이다. 과거 결과를 보고 잘 맞는 조건을 고르면
     과거에만 잘 맞는 규칙이 되기 때문이다.
  2. '평소 상승 비율'과 비교한다. 지수는 원래 오르는 날이 조금 더 많아서,
     "오를 확률 55%"가 평소 54%와 같다면 아무 정보가 없다.
  3. 5거래일 결과는 날마다 겹치므로(월~금, 화~월 …) 표본을 5로 나눠
     신뢰구간을 보수적으로 잡는다.
  4. 규칙 자체의 과거 성적은 '그날 이전 데이터만으로 판단했다면'으로
     다시 돌려서 잰다(워크포워드). 미래 데이터가 섞이면 성적이 부풀려진다.

이 결과는 과거 빈도일 뿐 미래를 보장하지 않으며, 개인의 자산 상황을
고려한 조언이 아니다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from brief.clock import label                  # noqa: E402

HORIZON = 5            # 5거래일 뒤
MIN_HISTORY = 500      # 판단을 시작하기 전에 쌓여 있어야 할 거래일 수
MIN_NEFF = 20          # 조건별 최소 유효 표본 (겹침 보정 후)
Z = 1.96               # 95% 신뢰구간
OVERLAP = HORIZON      # 겹치는 5일 구간 보정 — 표본을 이 수로 나눈다

# id: (이름, 오를 쪽 행동, 내릴 쪽 행동, 한국장 여부)
TARGETS = {
    "KOSPI":  ("코스피", "매수", "매도", True),
    "KOSDAQ": ("코스닥", "매수", "매도", True),
    "SPX":    ("S&P 500", "매수", "매도", False),
    "NASDAQ": ("나스닥", "매수", "매도", False),
    "USDKRW": ("원/달러 환율", "달러 매수", "달러 매도", True),
}


@dataclass
class Evidence:
    label: str            # "200일선 위"
    p: float              # 이 조건일 때 5일 뒤 상승 비율
    base: float           # 평소 상승 비율
    n: int                # 조건 충족 표본 수
    lo: float
    hi: float
    edge: int             # +1 상승 우위, -1 하락 우위, 0 차이 없음
    med: float | None = None      # 5일 수익률 중앙값(%)
    q25: float | None = None
    q75: float | None = None


@dataclass
class View:
    id: str
    name: str
    buy: str
    sell: str
    asof: str
    close: float
    stance: int                               # +1 / 0 / -1
    evidence: list[Evidence] = field(default_factory=list)
    conflicted: bool = False
    bt_n: int = 0                             # 과거에 방향을 말한 횟수
    bt_acc: float | None = None               # 그때 맞힌 비율
    bt_base: float | None = None              # 같은 기간 '무조건 상승'에 걸었을 때
    bt_start: str = ""

    @property
    def stance_label(self) -> str:
        if self.stance > 0:
            return f"{self.buy} 우위"
        if self.stance < 0:
            return f"{self.sell} 우위"
        return "관망 · 근거가 엇갈림" if self.conflicted else "관망 · 통계적 우위 없음"

    @property
    def lead(self) -> Evidence | None:
        """방향을 뒷받침하는 근거 중 평소와 가장 크게 다른 것."""
        cands = [e for e in self.evidence if e.edge == self.stance and self.stance != 0]
        return max(cands, key=lambda e: abs(e.p - e.base)) if cands else None

    @property
    def probability(self) -> float | None:
        """이 관점이 맞을 것으로 보는 과거 빈도 (매도 우위면 하락 비율)."""
        e = self.lead
        if e is None:
            return None
        return e.p if self.stance > 0 else 1 - e.p


def _wilson(p: pd.Series, n: pd.Series) -> tuple[pd.Series, pd.Series]:
    n = n.where(n > 0)
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    margin = Z * np.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return (center - margin).clip(0, 1), (center + margin).clip(0, 1)


def _load(conn, iid: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT o.trade_date, o.close, m.ma20, m.ma200, m.sigma, m.pct_52w, m.streak "
        "FROM observations o JOIN metrics m "
        "  ON o.trade_date = m.trade_date AND o.instrument = m.instrument "
        "WHERE o.instrument = ? ORDER BY o.trade_date", conn, params=(iid,))
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date")


def _vix(conn, index: pd.DatetimeIndex, korean: bool) -> pd.Series:
    """각 날짜에 '그 시점에 이미 알 수 있던' VIX 값.

    한국장 마감(15:30 KST) 시점에는 같은 날짜의 미국장이 아직 열리지 않았으므로
    전날까지의 VIX 만 쓴다.
    """
    v = pd.read_sql_query(
        "SELECT trade_date, close FROM observations WHERE instrument='VIX' "
        "ORDER BY trade_date", conn)
    if v.empty:
        return pd.Series(np.nan, index=index)
    v["trade_date"] = pd.to_datetime(v["trade_date"])
    v = v.set_index("trade_date")["close"]
    q80 = v.rolling(252, min_periods=120).quantile(0.8)
    q20 = v.rolling(252, min_periods=120).quantile(0.2)
    state = pd.Series(0, index=v.index).where(q80.notna())
    state[v > q80] = 1
    state[v < q20] = -1

    left = pd.DataFrame({"t": index})
    right = pd.DataFrame({"t": state.index, "vix_state": state.values})
    merged = pd.merge_asof(left, right, on="t", allow_exact_matches=not korean)
    return pd.Series(merged["vix_state"].values, index=index)


def _active_masks(df: pd.DataFrame, today: pd.Series) -> dict[str, pd.Series]:
    """오늘의 상태와 같은 쪽의 조건만 골라 과거 전체에 대한 불리언 시리즈로."""
    out: dict[str, pd.Series] = {}

    if pd.notna(today["ma200"]):
        up = today["close"] > today["ma200"]
        s = (df["close"] > df["ma200"]) if up else (df["close"] <= df["ma200"])
        out["200일선 위" if up else "200일선 아래"] = s & df["ma200"].notna()

    if pd.notna(today["ma20"]):
        up = today["close"] > today["ma20"]
        s = (df["close"] > df["ma20"]) if up else (df["close"] <= df["ma20"])
        out["20일선 위" if up else "20일선 아래"] = s & df["ma20"].notna()

    if pd.notna(today["sigma"]) and abs(today["sigma"]) >= 1.5:
        up = today["sigma"] > 0
        out["직전일 큰 상승(σ≥1.5)" if up else "직전일 큰 하락(σ≤−1.5)"] = (
            df["sigma"] >= 1.5 if up else df["sigma"] <= -1.5)

    if pd.notna(today["streak"]) and abs(today["streak"]) >= 3:
        up = today["streak"] > 0
        out["3일 이상 연속 상승" if up else "3일 이상 연속 하락"] = (
            df["streak"] >= 3 if up else df["streak"] <= -3)

    if pd.notna(today["pct_52w"]) and (today["pct_52w"] >= 90 or today["pct_52w"] <= 10):
        up = today["pct_52w"] >= 90
        out["52주 범위 상단(90%↑)" if up else "52주 범위 하단(10%↓)"] = (
            df["pct_52w"] >= 90 if up else df["pct_52w"] <= 10)

    if pd.notna(today.get("vix_state")) and today["vix_state"] != 0:
        up = today["vix_state"] > 0
        out["VIX 1년 중 상위 20%" if up else "VIX 1년 중 하위 20%"] = (
            df["vix_state"] == 1 if up else df["vix_state"] == -1)

    return {k: v.fillna(False).astype(bool) for k, v in out.items()}


def _walkforward(df: pd.DataFrame, masks: dict[str, pd.Series], y: pd.Series,
                 known: pd.Series) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """각 날짜 t 에서, t−HORIZON 까지 결과가 확정된 과거만으로 조건별 통계를 낸다."""
    yv = y.fillna(0.0)
    kn = known.astype(float)

    N = kn.cumsum().shift(HORIZON)
    K = (yv * kn).cumsum().shift(HORIZON)
    base = K / N

    stats = {}
    for name, m in masks.items():
        mk = m.astype(float) * kn
        n = mk.cumsum().shift(HORIZON)
        k = (mk * yv).cumsum().shift(HORIZON)
        p = k / n
        n_eff = n / OVERLAP                          # 겹치는 5일 구간 보정
        lo, hi = _wilson(p, n_eff)
        ok = (n_eff >= MIN_NEFF) & (N >= MIN_HISTORY)
        edge = pd.Series(0, index=df.index)
        edge[ok & (lo > base)] = 1
        edge[ok & (hi < base)] = -1
        stats[name] = pd.DataFrame({"p": p, "n": n, "lo": lo, "hi": hi, "edge": edge})

    return pd.concat(stats, axis=1) if stats else pd.DataFrame(index=df.index), base, N


def _stance_series(df: pd.DataFrame, y: pd.Series, known: pd.Series) -> pd.Series:
    """과거 각 날짜에 '그날의 조건'으로 이 규칙이 냈을 판단. 워크포워드 성적 계산용."""
    # 날짜마다 조건 쪽이 다르므로, 양쪽 조건을 모두 만들어 두고 그날 해당하는 쪽을 쓴다
    pairs = {
        "ma200": (df["close"] > df["ma200"], df["close"] <= df["ma200"], df["ma200"].notna()),
        "ma20": (df["close"] > df["ma20"], df["close"] <= df["ma20"], df["ma20"].notna()),
        "sigma": (df["sigma"] >= 1.5, df["sigma"] <= -1.5, df["sigma"].notna()),
        "streak": (df["streak"] >= 3, df["streak"] <= -3, df["streak"].notna()),
        "p52": (df["pct_52w"] >= 90, df["pct_52w"] <= 10, df["pct_52w"].notna()),
        "vix": (df["vix_state"] == 1, df["vix_state"] == -1, df["vix_state"].notna()),
    }
    masks = {}
    for key, (a, b, valid) in pairs.items():
        masks[f"{key}+"] = (a & valid).fillna(False)
        masks[f"{key}-"] = (b & valid).fillna(False)

    stats, _, _ = _walkforward(df, masks, y, known)
    pos = pd.Series(0, index=df.index)
    neg = pd.Series(0, index=df.index)
    for name, m in masks.items():
        e = stats[name]["edge"]
        pos += ((e == 1) & m).astype(int)
        neg += ((e == -1) & m).astype(int)

    stance = pd.Series(0, index=df.index)
    stance[(pos > 0) & (neg == 0)] = 1
    stance[(neg > 0) & (pos == 0)] = -1
    return stance


def build_view(conn, iid: str) -> View | None:
    name, buy, sell, korean = TARGETS[iid]
    df = _load(conn, iid)
    if len(df) < MIN_HISTORY + HORIZON * 2:
        return None
    df["vix_state"] = _vix(conn, df.index, korean)

    fwd = df["close"].shift(-HORIZON) / df["close"] - 1
    known = fwd.notna()
    y = (fwd > 0).astype(float).where(known)

    today = df.iloc[-1]
    masks = _active_masks(df, today)
    stats, base, _ = _walkforward(df, masks, y, known)

    evidence: list[Evidence] = []
    for label_, m in masks.items():
        row = stats[label_].iloc[-1]
        if pd.isna(row["p"]) or row["n"] / OVERLAP < MIN_NEFF:
            continue
        past = fwd[m & known] * 100
        evidence.append(Evidence(
            label=label_, p=float(row["p"]), base=float(base.iloc[-1]),
            n=int(row["n"]), lo=float(row["lo"]), hi=float(row["hi"]),
            edge=int(row["edge"]),
            med=float(past.median()) if len(past) else None,
            q25=float(past.quantile(0.25)) if len(past) else None,
            q75=float(past.quantile(0.75)) if len(past) else None,
        ))

    pos = sum(1 for e in evidence if e.edge > 0)
    neg = sum(1 for e in evidence if e.edge < 0)
    stance = 1 if pos and not neg else -1 if neg and not pos else 0

    # 규칙 자체의 과거 성적 (워크포워드)
    hist = _stance_series(df, y, known)
    said = (hist != 0) & known
    bt_n = int(said.sum())
    bt_acc = None
    bt_base = None
    if bt_n:
        right = ((hist > 0) & (y == 1.0)) | ((hist < 0) & (y == 0.0))
        bt_acc = float(right[said].mean())
        # 공정한 비교: 규칙이 방향을 말한 '바로 그날들'에 무조건 상승이라고 했다면 몇 % 맞았나
        bt_base = float(y[said].mean())

    return View(
        id=iid, name=name, buy=buy, sell=sell,
        asof=df.index[-1].strftime("%Y-%m-%d"), close=float(today["close"]),
        stance=stance, evidence=evidence, conflicted=bool(pos and neg),
        bt_n=bt_n, bt_acc=bt_acc, bt_base=bt_base,
        bt_start=label(hist[said].index[0].date()) if bt_n else "",
    )


def build_all(conn) -> list[View]:
    out = []
    for iid in TARGETS:
        try:
            v = build_view(conn, iid)
        except Exception:                                  # noqa: BLE001
            v = None
        if v:
            out.append(v)
    return out


if __name__ == "__main__":
    from brief import db
    with db.connect() as c:
        for v in build_all(c):
            print(f"\n■ {v.name} ({label(v.asof)} 종가 {v.close:,.2f}) → {v.stance_label}")
            for e in v.evidence:
                mark = {1: "▲", -1: "▼", 0: "·"}[e.edge]
                rng = f" | 5일 수익률 중앙 {e.med:+.2f}% (25~75%: {e.q25:+.2f}~{e.q75:+.2f})" if e.med is not None else ""
                print(f"   {mark} {e.label:<22} 5일 뒤 상승 {e.p:.0%} "
                      f"(평소 {e.base:.0%}, 95% {e.lo:.0%}~{e.hi:.0%}, 표본 {e.n}){rng}")
            if v.bt_n:
                print(f"   규칙 과거 성적: 방향 제시 {v.bt_n}회 중 적중 {v.bt_acc:.1%} "
                      f"(같은 기간 '무조건 상승' {v.bt_base:.1%}, {v.bt_start}부터)")
