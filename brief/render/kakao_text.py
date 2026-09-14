"""카카오톡 메시지 한 건 — 3줄 요약 + 링크.

카카오 텍스트 템플릿은 200자 제한이다. 줄 수를 3개로 고정하고 한 줄 길이에
상한을 두어 항상 제한 안에 들어오게 한다.

세 줄은 해석이 아니라 그날 실제 숫자다: 지수 등락 · 외국인 매매 · 업종 강약.
"""
from __future__ import annotations

LIMIT = 190          # 카카오는 이모지를 2자로 세기도 해서 여유를 둔다
LINE_MAX = 44        # 머리말·꼬리말을 합쳐도 LIMIT 을 넘지 않는 한 줄 상한
FOOTER = "종목·업종 상세는 아래 링크에서 확인하세요."


def _eok(v: float) -> str:
    a = abs(v)
    return f"{a / 10_000:.1f}조" if a >= 10_000 else f"{a:,.0f}억"


def _compose(date_short: str, candidates: list, title: str = "경제 브리핑") -> str:
    """후보를 앞에서부터 3줄 고른다.

    후보가 튜플이면 '같은 내용의 긴 형태, 짧은 형태'라는 뜻이다.
    들어가는 첫 형태 하나만 쓴다 — 둘 다 넣으면 같은 말이 두 번 나온다.
    상한을 넘는 문장은 자르지 않고 건너뛴다.
    """
    picked: list[str] = []
    for c in candidates:
        options = c if isinstance(c, tuple) else (c,)
        for o in options:
            o = (o or "").strip()
            if o and len(o) <= LINE_MAX and o not in picked:
                picked.append(o)
                break
        if len(picked) == 3:
            break

    body = "\n".join(f"{i}. {line}" for i, line in enumerate(picked, 1))
    text = f"📊 {date_short} {title}\n\n{body}\n\n{FOOTER}"
    assert len(text) <= LIMIT, f"카카오 제한 초과: {len(text)}자"
    return text


def _tiles(payload: dict, ids: tuple[str, ...]) -> str:
    short = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "SPX": "S&P", "NASDAQ": "나스닥"}
    parts = [f"{short[t['id']]} {t['change']}"
             for t in payload.get("tiles", []) if t["id"] in ids]
    return " · ".join(parts)


def _foreign_line(payload: dict) -> str:
    kr = (payload.get("detail") or {}).get("kr") or {}
    f = kr.get("foreign") or {}
    if f.get("buy") and f.get("sell"):
        b, s = f["buy"][0], f["sell"][0]
        return f"외국인 매수 {b['name']} {_eok(b['net_eok'])} · 매도 {s['name']} {_eok(s['net_eok'])}"
    return ""


def _foreign_total_line(payload: dict) -> str:
    for s in payload.get("flow_stats", []):
        if s.investor == "외국인합계" and s.market == "KOSPI":
            verb = "순매수" if s.net_buy > 0 else "순매도"
            run = f" ({abs(s.streak)}일째)" if abs(s.streak) >= 2 else ""
            return f"외국인 코스피 {_eok(s.eok)}원 {verb}{run}"
    return ""


def _sector_line(payload: dict) -> str:
    kr = (payload.get("detail") or {}).get("kr") or {}
    sec = kr.get("sectors") or []
    if len(sec) >= 2:
        hi, lo = sec[0], sec[-1]
        return f"업종 강세 {hi['name']} {hi['chg_pct']:+.1f}% · 약세 {lo['name']} {lo['chg_pct']:+.1f}%"
    return ""


def _us_line(payload: dict) -> str:
    us = (payload.get("detail") or {}).get("us") or {}
    big = us.get("big") or []
    if len(big) >= 2:
        hi, lo = big[0], big[-1]
        return f"미국 {hi['name']} {hi['chg_pct']:+.1f}% · {lo['name']} {lo['chg_pct']:+.1f}%"
    return ""


def build(payload: dict) -> str:
    """새 거래일 데이터가 있는 날."""
    candidates = [
        (_tiles(payload, ("KOSPI", "KOSDAQ", "SPX", "NASDAQ")),
         _tiles(payload, ("KOSPI", "KOSDAQ", "SPX"))),         # 넘치면 짧은 형태
        _foreign_line(payload),
        _sector_line(payload),
        _foreign_total_line(payload),
        _us_line(payload),
        payload["basis"],
    ]
    return _compose(payload["date_short"], candidates)


def build_no_new_data(payload: dict, weekday: int) -> str:
    """휴장이라 새로 마감된 거래가 없는 날. 같은 내용을 새 브리핑인 척 보내지 않는다."""
    reason = "주말이라" if weekday in (5, 6, 0) else "휴장으로"
    last = payload["basis"].replace(" 마감 기준", "")
    candidates = [
        f"{reason} 새로 마감된 거래가 없습니다.",
        f"마지막 마감: {last}",
        "다음 거래일 마감 후 새 브리핑이 옵니다.",
    ]
    return _compose(payload["date_short"], candidates)


def _short_event(e) -> str:
    """카톡 한 줄에 들어갈 짧은 일정 이름."""
    title = e.title.replace(" 금리 결정", "")
    if e.region == "US" and not title.startswith("FOMC"):
        title = "미국 " + title
    return title


def build_weekly(payload: dict) -> str:
    """월요일 아침 — 지난주 정리 + 이번 주 일정."""
    week = payload["week"]
    moves = {m.id: m for m in week.moves} if week else {}
    candidates: list[str] = []

    kr, kq, us = moves.get("KOSPI"), moves.get("KOSDAQ"), moves.get("SPX")
    if kr and us:
        tail = f" · 코스닥 {kq.change_text}" if kq else ""
        candidates.append((f"지난주 코스피 {kr.change_text}{tail} · S&P {us.change_text}",
                           f"지난주 코스피 {kr.change_text} · S&P {us.change_text}"))

    key = sorted([e for e in payload["events"] if e.importance >= 2],
                 key=lambda e: (-e.importance, e.day))[:2]
    if key:
        key.sort(key=lambda e: e.day)
        candidates.append("이번 주: " + " · ".join(
            f"{e.when().split(' ')[0]} {_short_event(e)}" for e in key))

    if week:
        for f in week.flows[:1]:
            verb = "순매수" if f["total_eok"] > 0 else "순매도"
            candidates.append(f"외국인 {f['market']} 주간 {_eok(f['total_eok'])}원 {verb}")

    candidates += [_sector_line(payload), payload["basis"]]
    return _compose(payload["date_short"], candidates, title="주간 브리핑")
