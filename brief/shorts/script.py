"""쇼츠 대본 — 그날 카톡으로 보내는 내용을 캐릭터가 브리핑하듯 읽는다.

동화님 결정(2026-09-22): "그날 카톡 보내는 내용 그대로를 브리핑하는 것처럼 대본을 만들어라."

  화면 글자  카톡 메시지의 줄을 그대로 쓴다.
  읽는 말    같은 줄을 만든 숫자에서 문장을 만든다. 카톡 줄 글자를 다시 해석하지 않고,
             그 줄을 만든 후보(지수·외국인·업종…)를 찾아 원래 값으로 말을 만든다 —
             '-0.23%' 같은 글자를 뜯어 읽다 부호나 단위를 잘못 읽는 일을 막는다.

  자켓 색    코스피가 오른 날(주간은 지난주 코스피가 오른 주) 빨강, 내린 날 파랑.
  놀람 표정  지수 줄에서 코스피·코스닥·S&P·나스닥 중 하나라도 '평소 하루 변동폭의
             SURPRISE_SIGMA 배' 이상 움직인 날에만. 브리핑의 '이례적 변동'과 같은 잣대다.

새 문장을 지어내지 않는다. 모든 숫자는 카톡에 실린 값과 같은 출처에서 온다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from brief.interpret.rules import josa
from brief.render import kakao_text as kt

SURPRISE_SIGMA = 2.0
WEEKDAY = "월화수목금토일"
INDEX_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "SPX": "S&P 500", "NASDAQ": "나스닥"}


@dataclass
class Segment:
    screen: str                 # 화면 글자 (카톡 줄 그대로)
    speech: str                 # 읽는 말
    kind: str = ""
    surprise: bool = False


@dataclass
class Script:
    date_label: str             # "9/22(화)"
    title: str                  # "경제 브리핑" / "주간 브리핑"
    mood: str                   # "up" / "down"
    segments: list[Segment] = field(default_factory=list)
    link: str = ""

    @property
    def text(self) -> str:
        return " ".join(s.speech for s in self.segments)


# ── 숫자를 말로 ─────────────────────────────────────────────

def _pct_word(v: float, digits: int = 2) -> str:
    return f"{abs(v):.{digits}f}퍼센트"


def _move(v: float, up: str = "올랐", down: str = "내렸") -> str:
    return up if v > 0 else down if v < 0 else "제자리였"


def _won(eok: float) -> str:
    return kt._eok(eok) + " 원"


def _num(change: str) -> float | None:
    t = change.replace("%", "").replace("−", "-").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


# ── 줄 종류별 읽는 말 ───────────────────────────────────────

def _say_tiles(payload: dict, ids: tuple[str, ...]) -> str:
    parts = []
    for t in payload.get("tiles", []):
        if t["id"] not in ids:
            continue
        v = _num(t["change"])
        if v is None:
            continue
        name = INDEX_NAME[t["id"]]
        head = f"{name}{josa(name, '은/는')}"
        parts.append(f"{head} 보합이었" if v == 0 else f"{head} {_pct_word(v)} {_move(v)}")
    # 한국 둘, 미국 둘씩 한 문장으로: "코스피는 … 내렸고, 코스닥은 … 올랐습니다."
    return " ".join("고, ".join(parts[i:i + 2]) + "습니다." for i in range(0, len(parts), 2))


def _say_foreign(payload: dict) -> str:
    f = ((payload.get("detail") or {}).get("kr") or {}).get("foreign") or {}
    b, s = f["buy"][0], f["sell"][0]
    return (f"외국인은 {b['name']}{josa(b['name'], '을/를')} {_won(b['net_eok'])}어치 가장 많이 샀고, "
            f"{s['name']}{josa(s['name'], '을/를')} {_won(s['net_eok'])}어치 가장 많이 팔았습니다.")


def _say_foreign_total(payload: dict) -> str:
    for s in payload.get("flow_stats", []):
        if s.investor == "외국인합계" and s.market == "KOSPI":
            verb = "순매수했습니다" if s.net_buy > 0 else "순매도했습니다"
            run = f" {abs(s.streak)}거래일 연속입니다." if abs(s.streak) >= 2 else ""
            return f"외국인은 코스피에서 {_won(s.eok)}을 {verb}.{run}"
    return ""


def _say_sector(payload: dict) -> str:
    sec = ((payload.get("detail") or {}).get("kr") or {}).get("sectors") or []
    hi, lo = sec[0], sec[-1]
    return (f"업종별로는 {hi['name']}{josa(hi['name'], '이/가')} {_pct_word(hi['chg_pct'], 1)} "
            f"{_move(hi['chg_pct'])}고, {lo['name']}{josa(lo['name'], '은/는')} "
            f"{_pct_word(lo['chg_pct'], 1)} {_move(lo['chg_pct'])}습니다.")


def _say_us(payload: dict) -> str:
    big = ((payload.get("detail") or {}).get("us") or {}).get("big") or []
    hi, lo = big[0], big[-1]
    return (f"미국 대형주 중에서는 {hi['name']}{josa(hi['name'], '이/가')} {_pct_word(hi['chg_pct'], 1)} "
            f"{_move(hi['chg_pct'])}고, {lo['name']}{josa(lo['name'], '은/는')} "
            f"{_pct_word(lo['chg_pct'], 1)} {_move(lo['chg_pct'])}습니다.")


def _surprise_tiles(payload: dict, ids: tuple[str, ...]) -> bool:
    for r in payload.get("dashboard", []):
        if r.get("id") in ids:
            try:
                if abs(float(r.get("sigma"))) >= SURPRISE_SIGMA:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _daily_candidates(payload: dict) -> list[tuple[str, str, callable]]:
    """카톡 build() 와 같은 순서·같은 글자의 후보. (종류, 화면 글자, 읽는 말 만들기)"""
    four, three = ("KOSPI", "KOSDAQ", "SPX", "NASDAQ"), ("KOSPI", "KOSDAQ", "SPX")
    return [
        ("tiles", kt._tiles(payload, four), lambda: _say_tiles(payload, four)),
        ("tiles", kt._tiles(payload, three), lambda: _say_tiles(payload, three)),
        ("foreign", kt._foreign_line(payload), lambda: _say_foreign(payload)),
        ("sector", kt._sector_line(payload), lambda: _say_sector(payload)),
        ("foreign_total", kt._foreign_total_line(payload), lambda: _say_foreign_total(payload)),
        ("us", kt._us_line(payload), lambda: _say_us(payload)),
        ("basis", payload.get("basis", ""), lambda: f"{payload.get('basis', '')}입니다."),
    ]


def _weekly_candidates(payload: dict) -> list[tuple[str, str, callable]]:
    """카톡 build_weekly() 의 줄을 다시 만들어 짝지운다."""
    week = payload.get("week")
    moves = {m.id: m for m in week.moves} if week else {}
    out = []
    kr, kq, us = moves.get("KOSPI"), moves.get("KOSDAQ"), moves.get("SPX")
    if kr and us:
        def say(with_kq: bool) -> str:
            items = [("코스피", kr)] + ([("코스닥", kq)] if with_kq and kq else []) + [("S&P 500", us)]
            words = []
            for name, m in items:
                v = _num(m.change_text)
                words.append(f"{name}{josa(name, '은/는')} {_pct_word(v)} {_move(v)}" if v is not None
                             else f"{name}{josa(name, '은/는')} {m.change_text}")
            return "지난주 " + "고, ".join(words) + "습니다."
        tail = f" · 코스닥 {kq.change_text}" if kq else ""
        out.append(("week_moves", f"지난주 코스피 {kr.change_text}{tail} · S&P {us.change_text}",
                    lambda: say(True)))
        out.append(("week_moves", f"지난주 코스피 {kr.change_text} · S&P {us.change_text}",
                    lambda: say(False)))

    key = sorted([e for e in payload.get("events", []) if e.importance >= 2],
                 key=lambda e: (-e.importance, e.day))[:2]
    if key:
        key.sort(key=lambda e: e.day)
        line = "이번 주: " + " · ".join(f"{e.when().split(' ')[0]} {kt._short_event(e)}" for e in key)
        speech = "이번 주에는 " + ", ".join(
            f"{e.day.month}월 {e.day.day}일 {kt._short_event(e)}" for e in key) + " 일정이 있습니다."
        out.append(("week_events", line, lambda s=speech: s))

    if week:
        for f in week.flows[:1]:
            verb = "순매수" if f["total_eok"] > 0 else "순매도"
            line = f"외국인 {f['market']} 주간 {kt._eok(f['total_eok'])}원 {verb}"
            market = "코스피" if f["market"] == "KOSPI" else "코스닥" if f["market"] == "KOSDAQ" else f["market"]
            speech = f"외국인은 지난주 {market}에서 {_won(f['total_eok'])}을 {verb}했습니다."
            out.append(("week_flow", line, lambda s=speech: s))
    out.append(("sector", kt._sector_line(payload), lambda: _say_sector(payload)))
    out.append(("basis", payload.get("basis", ""), lambda: f"{payload.get('basis', '')}입니다."))
    return out


def build(payload: dict, message: str, weekly: bool = False, link: str = "") -> Script | None:
    """카톡 메시지와 같은 날 payload 로 대본을 만든다. 새 거래가 없는 날은 None."""
    lines = [l.split(". ", 1)[1] for l in message.splitlines()
             if len(l) > 3 and l[0].isdigit() and l[1:3] == ". "]
    if not lines:
        return None
    cands = _weekly_candidates(payload) if weekly else _daily_candidates(payload)

    segs: list[Segment] = []
    for line in lines:
        match = next((c for c in cands if c[1] and c[1] == line), None)
        if match is None:
            # 짝을 못 찾으면 그 줄은 읽지 않는다 — 글자를 대충 읽다 틀리느니 빼는 게 낫다
            continue
        kind, screen, say = match
        speech = say()
        if not speech:
            continue
        surprise = kind == "tiles" and _surprise_tiles(payload, ("KOSPI", "KOSDAQ", "SPX", "NASDAQ"))
        segs.append(Segment(screen, speech, kind, surprise))
    if not segs:
        return None

    from datetime import date
    d = date.fromisoformat(payload["brief_date"])
    title = "주간 브리핑" if weekly else "경제 브리핑"
    intro = Segment(f"{payload['date_short']} {title}",
                    f"{d.month}월 {d.day}일 {WEEKDAY[d.weekday()]}요일 {title}입니다.", "intro")
    outro = Segment("전체 브리핑은 설명란 링크에서",
                    "종목과 업종 상세는 설명란 링크에서 확인하세요.", "outro")

    # 자켓 색 — 코스피 방향. 주간이면 지난주 코스피, 아니면 오늘 코스피.
    mood = "up"
    if weekly and payload.get("week"):
        m = next((m for m in payload["week"].moves if m.id == "KOSPI"), None)
        v = _num(m.change_text) if m else None
        mood = "down" if v is not None and v < 0 else "up"
    else:
        t = next((t for t in payload.get("tiles", []) if t["id"] == "KOSPI"), None)
        v = _num(t["change"]) if t else None
        mood = "down" if v is not None and v < 0 else "up"

    return Script(payload["date_short"], title, mood, [intro, *segs, outro], link)
