"""핵심 거시 이슈와 '과거 데이터로 본 참고' — 매일 데이터로 자동 계산 (9/29 동화님).

동화님이 원한 것: 매일 '핵심 거시 이슈 및 시사점'과 '투자 관점 아이디어'.
만든 방식 (동화님 합의):
  이슈     오늘 평소 하루 변동폭의 1.5배 넘게 움직였거나(σ), 지난 1년 중 최고·최저 근처(52주 위치
           95% 이상·5% 이하)에 닿은 지표에서 최대 3개. 숫자는 대시보드와 같은 값.
  시사점   그 지표에 대한 '일반적인 관계'(교과서 수준)만 쓴다 — 오늘 움직인 원인이라고 단정하지
           않는다. 원인은 데이터로 확인할 수 없다.
  참고     투자 조언(사라·팔아라·비중) 대신 실제 통계: 과거에 같은 조건이 시작된 날 뒤
           20거래일 동안 코스피·S&P 500이 오른 비율과 중앙값, 그리고 '평소(모든 날)' 비율.
           같은 조건이 며칠씩 이어지면 표본이 부풀어서, 조건이 새로 시작된 날만 센다(에피소드).
           표본이 MIN_EPISODES 보다 적으면 통계를 내지 않는다.

원자재 선물처럼 월물 교체로 가짜 변동이 생기는 지표(can_claim=False)는 이슈로 쓰지 않는다.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import median

HORIZON = 20             # 거래일
MIN_EPISODES = 8
SIGMA_ISSUE = 1.5        # 리포트 '오늘 볼 것'과 같은 기준
BAND_HI, BAND_LO = 95, 5
EPISODE_GAP_DAYS = 30    # 조건이 끊긴 뒤 이만큼(달력일) 지나 다시 나타나야 새 에피소드
TARGETS = [("KOSPI", "코스피"), ("SPX", "S&P 500")]

# 지표별 일반적인 관계 — (오를 때, 내릴 때). 오늘의 원인 설명이 아니다.
RELATION = {
    "rate": ("금리가 오르면 일반적으로 미래 이익의 현재가치가 줄어 성장주 평가에 부담이 되고, "
             "예금·채권의 상대적 매력이 커집니다.",
             "금리가 내리면 일반적으로 성장주 평가 부담이 줄고, 대출 이자 부담도 가벼워집니다."),
    "USDKRW": ("원/달러 환율이 오르면(원화 약세) 일반적으로 수출기업의 원화 환산 이익에는 유리하고, "
               "수입 물가에는 부담이 됩니다. 외국인 투자자에게는 환차손 우려가 커집니다.",
               "원/달러 환율이 내리면(원화 강세) 일반적으로 수입 물가 부담이 줄고, "
               "외국인 투자자에게는 환차익 기대가 생깁니다."),
    "DXY": ("달러가 강해지면 일반적으로 신흥국 통화가 약해지고 신흥국에서 자금이 빠지는 압력이 커집니다.",
            "달러가 약해지면 일반적으로 신흥국 통화와 위험자산에 우호적인 환경이 됩니다."),
    "VIX": ("VIX(변동성 지수)가 오르면 옵션 시장이 앞으로의 큰 등락을 더 많이 반영하고 있다는 뜻으로, "
            "시장 불안이 커졌다는 신호로 읽힙니다.",
            "VIX가 내리면 옵션 시장이 앞으로의 등락을 작게 보고 있다는 뜻으로, 불안이 줄었다는 신호로 읽힙니다."),
    "index": ("", ""),
}
# 같은 성격의 이슈는 하나만 (S&P 500·나스닥이 함께 크게 오른 날 두 칸을 쓰지 않게)
FAMILY = {"US10Y": "us_rate", "US30Y": "us_rate", "US02Y": "us_rate", "US03M": "us_rate",
          "KTB3Y": "kr_rate", "KTB10Y": "kr_rate", "USDKRW": "krw", "DXY": "usd", "VIX": "vix",
          "KOSPI": "kr_index", "KOSDAQ": "kr_index", "SPX": "us_index", "NASDAQ": "us_index",
          "DOW": "us_index", "RUSSELL2000": "us_index"}
# 경제 신호 → 주제별 뉴스(brief/collect/theme_news.py) 의 주제
THEME_OF = {"US10Y": "금리·연준", "US30Y": "금리·연준", "US02Y": "금리·연준", "US03M": "금리·연준",
            "KTB3Y": "금리·연준", "KTB10Y": "금리·연준", "USDKRW": "환율", "DXY": "환율"}
KIND_OF = {"US10Y": "rate", "US30Y": "rate", "US02Y": "rate", "US03M": "rate", "KTB3Y": "rate",
           "KTB10Y": "rate", "USDKRW": "USDKRW", "DXY": "DXY", "VIX": "VIX",
           "KOSPI": "index", "KOSDAQ": "index", "SPX": "index", "NASDAQ": "index", "DOW": "index",
           "RUSSELL2000": "index"}


def _closes(conn, iid: str) -> list[tuple[str, float]]:
    return [(r["trade_date"], r["close"]) for r in conn.execute(
        "SELECT trade_date, close FROM observations WHERE instrument=? AND close IS NOT NULL "
        "ORDER BY trade_date", (iid,)).fetchall()]


def _forward(series: list[tuple[str, float]], start: str) -> float | None:
    """start 날(또는 그 전 마지막 거래일) 종가 대비 HORIZON 거래일 뒤 수익률(%)."""
    idx = None
    for i, (d, _) in enumerate(series):
        if d <= start:
            idx = i
        else:
            break
    if idx is None or idx + HORIZON >= len(series):
        return None
    return (series[idx + HORIZON][1] / series[idx][1] - 1) * 100


def _episodes(dates: list[str]) -> list[str]:
    out, last = [], None
    for d in dates:
        dd = date.fromisoformat(d)
        if last is None or dd - last > timedelta(days=EPISODE_GAP_DAYS):
            out.append(d)
        last = dd
    return out


def _stats(rets: list[float]) -> dict | None:
    rets = [r for r in rets if r is not None]
    if len(rets) < MIN_EPISODES:
        return None
    return {"n": len(rets), "up": round(sum(r > 0 for r in rets) / len(rets) * 100),
            "median": round(median(rets), 1)}


def _baseline(series, start: str) -> dict | None:
    """같은 기간 모든 날 기준 — '평소엔 이 정도'."""
    rets = [_forward(series, d) for d, _ in series if d >= start]
    return _stats(rets)


def build(conn, snapshot: list, insts: dict, today_basis: str = "") -> list[dict]:
    """오늘의 핵심 거시 이슈 최대 3개와 각각의 과거 통계."""
    cands = []
    for r in snapshot:
        inst = insts.get(r["instrument"])
        iid = r["instrument"]
        if not inst or not inst.can_claim or iid not in KIND_OF:
            continue
        s, p = r["sigma"], r["pct_52w"]
        if s is not None and abs(s) >= SIGMA_ISSUE:
            cond = ("sigma >= ?", SIGMA_ISSUE) if s > 0 else ("sigma <= ?", -SIGMA_ISSUE)
            cands.append((abs(s), iid, inst, r, "sigma", cond, s > 0))
        elif p is not None and (p >= BAND_HI or p <= BAND_LO):
            cond = ("pct_52w >= ?", BAND_HI) if p >= BAND_HI else ("pct_52w <= ?", BAND_LO)
            cands.append((1.4, iid, inst, r, "band", cond, p >= BAND_HI))
    cands.sort(key=lambda c: -c[0])

    picked, fams = [], set()
    for c in cands:
        if FAMILY[c[1]] in fams:
            continue
        fams.add(FAMILY[c[1]])
        picked.append(c)
    series = {t: _closes(conn, t) for t, _ in TARGETS}
    out = []
    for score, iid, inst, r, kind, (where, val), up in picked[:3]:
        move = (f"{r['chg_bp']:+.0f}bp" if inst.kind == "rate" else f"{r['chg_pct']:+.2f}%")
        close = f"{r['close']:,.{inst.decimals}f}{'%' if inst.kind == 'rate' else ''}"
        if kind == "sigma":
            title = f"{inst.name} {move} — 평소 하루 변동폭의 {abs(r['sigma']):.1f}배"
            fact = (f"{inst.name}{'가' if _no_batchim(inst.name) else '이'} {close}{_ro(close)} 하루 {move} "
                    f"움직였습니다. 직전 60거래일 하루 변동폭의 {abs(r['sigma']):.1f}배입니다.")
            cond_ko = f"{inst.name}{'가' if _no_batchim(inst.name) else '이'} 하루에 평소의 1.5배 넘게 {'오른' if up else '내린'} 날"
        else:
            where_ko = "가장 높은" if up else "가장 낮은"
            title = f"{inst.name} {close} — 지난 1년 중 {where_ko} 수준 근처"
            fact = (f"{inst.name}{'가' if _no_batchim(inst.name) else '이'} {close}{_ro(close)} 지난 1년 범위의 "
                    f"{r['pct_52w']:.0f}% 위치입니다(0% = 1년 최저, 100% = 1년 최고).")
            cond_ko = f"{inst.name}{'가' if _no_batchim(inst.name) else '이'} 1년 중 {where_ko} 수준({'상위' if up else '하위'} 5%)에 새로 들어선 날"
        rel_up, rel_down = RELATION[KIND_OF[iid]]
        relation = rel_up if up else rel_down

        dates = [x["trade_date"] for x in conn.execute(
            f"SELECT trade_date FROM metrics WHERE instrument=? AND {where} ORDER BY trade_date",
            (iid, val)).fetchall()]
        eps = _episodes([d for d in dates if d < r["trade_date"]])     # 오늘은 빼고 과거만
        stats = []
        for t, tname in TARGETS:
            st = _stats([_forward(series[t], d) for d in eps])
            if st:
                start = series[t][0][0] if series[t] else ""
                stats.append({"target": tname, **st, "base": _baseline(series[t], start)})
        out.append({"id": iid, "name": inst.name, "title": title, "fact": fact, "relation": relation,
                    "cond": cond_ko, "episodes": len(eps), "stats": stats,
                    "since": dates[0][:4] if dates else "", "up": up})
    return out


def _no_batchim(word: str) -> bool:
    last = word.rstrip(")").rstrip()[-1:]
    if not last:
        return True
    if last.isdigit():
        return last not in "0136780"
    if not ("가" <= last <= "힣"):
        return True
    return (ord(last) - 0xAC00) % 28 == 0


def _ro(num_text: str) -> str:
    """숫자 뒤 '으로/로' — 읽는 소리로 (…0 십·영, …3 삼, …6 육 은 받침이 있고 ㄹ 이 아니다)."""
    last = num_text[-1:]
    return "으로" if last in "036" else "로"
