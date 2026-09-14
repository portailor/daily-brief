"""카카오톡 메시지 한 건 — 항상 '3줄 요약 + 링크 안내' 형태.

카카오 텍스트 템플릿은 200자 제한이다. 들어가는 만큼 채우는 방식은
날마다 줄 수가 달라지고 어중간한 데서 끊겨 오히려 읽기 불편했다.
그래서 줄 수를 3개로 고정하고, 각 줄의 길이를 상한 안에 두어
전체가 항상 제한 안에 들어오게 한다. 나머지는 링크에서 본다.
"""
from __future__ import annotations

LIMIT = 190          # 카카오는 이모지를 2자로 세기도 해서 여유를 둔다
LINE_MAX = 46        # 머리말·꼬리말을 합쳐도 LIMIT 을 넘지 않는 한 줄 상한
FOOTER = "자세한 내용은 아래 링크에서 확인하세요."


def _eok(v: float) -> str:
    a = abs(v)
    return f"{a / 10_000:.1f}조원" if a >= 10_000 else f"{a:,.0f}억원"


def _compose(date_short: str, candidates: list[str], title: str = "경제 브리핑") -> str:
    """후보를 앞에서부터 3줄 고른다. 상한을 넘는 문장은 자르지 않고 건너뛴다."""
    picked: list[str] = []
    for c in candidates:
        c = c.strip()
        if c and len(c) <= LINE_MAX and c not in picked:
            picked.append(c)
        if len(picked) == 3:
            break

    body = "\n".join(f"{i}. {line}" for i, line in enumerate(picked, 1))
    text = f"📊 {date_short} {title}\n\n{body}\n\n{FOOTER}"
    assert len(text) <= LIMIT, f"카카오 제한 초과: {len(text)}자"
    return text


def build(payload: dict) -> str:
    """새 거래일 데이터가 있는 날."""
    candidates: list[str] = [payload["verdict"]["headline"]]

    # 외국인 수급이 며칠째 한 방향이면 그게 두 번째로 중요하다
    for s in payload["flow_stats"]:
        if s.investor == "외국인합계" and abs(s.streak) >= 2:
            verb = "순매수" if s.net_buy > 0 else "순매도"
            candidates.append(
                f"외국인이 {s.market}에서 {_eok(s.eok)} {verb}, {abs(s.streak)}일째입니다.")
            break

    # 결론에 쓴 지표 다음으로 이례적이었던 것
    for r in payload["notable_rows"][1:2]:
        candidates.append(f"{r['name']}도 {r['change']}, 평소의 {abs(r['sigma']):.1f}배였습니다.")

    card, track = payload["score"], payload["track"]
    if card.hit + card.miss:
        tail = f" (누적 {track['accuracy']:.0%})" if track.get("total") else ""
        candidates.append(f"지난 예측 {card.hit}/{card.hit + card.miss} 적중{tail}")

    for t in payload["triggers"][:1]:
        p = f" — 과거 확률 {t.probability:.0%}" if t.probability is not None else ""
        candidates.append(f"주목: {t.claim}{p}")

    # 위에서 3줄이 안 채워져도 항상 채울 수 있는 줄
    candidates.append(payload["basis"])

    return _compose(payload["date_short"], candidates)


def build_no_new_data(payload: dict, weekday: int) -> str:
    """주말·휴장이라 새로 마감된 거래가 없는 날. 같은 내용을 새 브리핑인 척 보내지 않는다."""
    reason = "주말이라" if weekday in (5, 6, 0) else "휴장으로"
    last = payload["basis"].replace(" 마감 기준", "")
    candidates = [
        f"{reason} 새로 마감된 거래가 없습니다.",
        f"마지막 마감: {last}",
        f"지난 결론: {payload['verdict']['headline']}",
        "다음 거래일 마감 후 새 브리핑이 옵니다.",
    ]
    return _compose(payload["date_short"], candidates)


def _short_event(e) -> str:
    """카톡 한 줄에 들어갈 짧은 일정 이름. 화면과 달리 국가 표시가 없으므로 붙인다."""
    title = e.title.replace(" 금리 결정", "")
    if e.region == "US" and not title.startswith("FOMC"):
        title = "미국 " + title
    return title


def build_weekly(payload: dict) -> str:
    """월요일 아침 — 지난주 정리 + 이번 주 준비."""
    week = payload["week"]
    moves = {m.id: m for m in week.moves} if week else {}
    candidates: list[str] = []

    kr, us = moves.get("KOSPI"), moves.get("SPX")
    if kr and us:
        candidates.append(f"지난주 코스피 {kr.change_text} · S&P500 {us.change_text}")

    # 이번 주 핵심 일정 — 한 주를 준비하는 게 월요일 메시지의 목적이다
    key = sorted([e for e in payload["events"] if e.importance >= 2],
                 key=lambda e: (-e.importance, e.day))[:2]
    if key:
        key.sort(key=lambda e: e.day)
        candidates.append("이번 주: " + " · ".join(
            f"{e.when().split(' ')[0]} {_short_event(e)}" for e in key))

    if week:
        for f in week.flows[:1]:
            verb = "순매수" if f["total_eok"] > 0 else "순매도"
            candidates.append(f"외국인 {f['market']} 주간 {_eok(f['total_eok'])} {verb}"
                              f" ({f['days']}일 중 {f['sell_days']}일 매도)")
        if week.top_day:
            t = week.top_day
            candidates.append(f"가장 이례적인 날: {t['day']} {t['name']} {t['move']}")
        if week.pred_total:
            candidates.append(f"지난주 예측 {week.pred_hit}/{week.pred_total} 적중")

    candidates.append(payload["basis"])
    return _compose(payload["date_short"], candidates, title="주간 브리핑")


if __name__ == "__main__":
    S = type("S", (), {})
    card = S(); card.hit, card.miss = 2, 1
    flow = S()
    flow.investor, flow.market, flow.eok, flow.net_buy, flow.streak = \
        "외국인합계", "KOSPI", -21122, -1, -3
    trig = S(); trig.claim, trig.probability = "코스피가 6,900 아래로 마감", 0.35

    payload = {
        "date_short": "9/15(화)",
        "verdict": {"headline": "미 국채 2년물이 평소의 2.7배로 급등했습니다."},
        "notable_rows": [
            {"name": "미 국채 2년물", "change": "+13bp", "sigma": 2.7},
            {"name": "국고채 3년물", "change": "+8bp", "sigma": 2.2},
        ],
        "flow_stats": [flow], "score": card,
        "track": {"total": 12, "accuracy": 0.583},
        "triggers": [trig],
        "basis": "한국 9/11(금) · 미국 9/11(금) 마감 기준",
    }
    quiet = dict(payload, verdict={"headline": "오늘은 평소와 다른 움직임이 없습니다. 특별히 할 일이 없는 날입니다."},
                 notable_rows=[], flow_stats=[], triggers=[])
    for title, msg in [("거래일", build(payload)),
                       ("조용한 거래일", build(quiet)),
                       ("주말", build_no_new_data(dict(payload, date_short="9/13(일)"), 6))]:
        print(f"── {title} ({len(msg)}자 / {LIMIT}) " + "─" * 20)
        print(msg, "\n")
