"""쇼츠 대본 — 주식 탭 전체 브리핑을 캐릭터가 읽는다.

동화님 결정
  9/22 23시  "그날 카톡 내용 그대로를 브리핑하듯" → 9/22 23:30 "전체 브리핑 가능하게"로 넓힘.
             말투는 아나운서처럼 또박또박하지 않아도 된다 — 캐릭터에 어울리게 해요체.

  화면     구간마다 이름표(한국 증시·환율과 금리…)와 '이름 — 값' 줄 몇 개.
  읽는 말  같은 숫자에서 만든 문장. 새 사실을 지어내지 않는다 — 모든 값은 그날 payload
           (리포트 페이지와 같은 출처)에서 온다. 값이 없는 구간은 통째로 뺀다.
  길이     쇼츠는 3분까지. 우선순위(priority, 클수록 덜 중요)가 큰 구간부터 빼서
           render 가 MAX_SEC 안에 맞춘다.

  자켓 색    코스피가 오른 날(주간은 지난주 코스피가 오른 주) 빨강, 내린 날 파랑.
  놀람 표정  그 구간 지표가 '평소 하루 변동폭의 SURPRISE_SIGMA 배' 이상 움직였을 때만.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from brief.interpret.rules import josa
from brief.render import kakao_text as kt

SURPRISE_SIGMA = 2.0
WEEKDAY = "월화수목금토일"
NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "SPX": "S&P 500", "NASDAQ": "나스닥",
        "DOW": "다우", "USDKRW": "원/달러 환율", "US10Y": "미국 10년물 금리",
        "KTB3Y": "국고채 3년물", "WTI": "WTI 유가", "GOLD": "금", "VIX": "VIX 공포지수"}
SPOKEN = {"원/달러 환율": "원 달러 환율", "VIX 공포지수": "빅스 공포지수"}


@dataclass
class Segment:
    tag: str                                   # 화면 이름표
    rows: list[tuple[str, str]]                # (이름, 값) — 값의 +/- 는 빨강/파랑
    speech: str                                # 읽는 말
    kind: str = ""
    note: str = ""                             # 줄 아래 작은 글
    surprise: bool = False
    priority: int = 1                          # 클수록 덜 중요. 길면 큰 것부터 뺀다

    @property
    def screen(self) -> str:                   # 설명란에 쓰는 한 줄 요약
        return " · ".join(f"{a} {b}".strip() for a, b in self.rows) or self.note


@dataclass
class Script:
    date_label: str
    title: str
    mood: str
    segments: list[Segment] = field(default_factory=list)
    link: str = ""

    @property
    def text(self) -> str:
        return " ".join(s.speech for s in self.segments)


# ── 말 다듬기 ───────────────────────────────────────────────

def _batchim(ch: str) -> bool:
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


def haeyo(s: str) -> str:
    """리포트의 합니다체 문장을 해요체로. 모르는 끝은 그대로 둔다."""
    for a, b in (("했습니다", "했어요"), ("있습니다", "있어요"), ("없습니다", "없어요"),
                 ("합니다", "해요"), ("됩니다", "돼요"), ("밑돕니다", "밑돌아요"),
                 ("넘어섭니다", "넘어서요")):
        s = s.replace(a, b)
    s = re.sub(r"(.)입니다", lambda m: m.group(1) + ("이에요" if _batchim(m.group(1)) else "예요"), s)
    s = re.sub(r"(\S)습니다", r"\1어요", s)       # 올랐습니다 → 올랐어요 등 남은 것
    return s


def _ieyo(word: str) -> str:
    """'삼성전자예요' / '우리금융지주예요' / '광전자예요' — 받침 있으면 '이에요'."""
    last = word[-1] if word else ""
    if last.isdigit():
        return "이에요" if last in "0136780" else "예요"
    return "이에요" if _batchim(last) else "예요"


def _say(text: str) -> str:
    """화면 글자를 소리 내 읽기 좋게 — 기호를 말로."""
    for a, b in (("%p", "퍼센트포인트"), ("%", "퍼센트"), ("~", "에서 "), (" · ", ", "), ("·", ", "),
                 ("−", "마이너스 ")):
        text = text.replace(a, b)
    for a, b in SPOKEN.items():
        text = text.replace(a, b)
    return text


def _pct(v: float, digits: int = 2) -> str:
    return f"{abs(v):.{digits}f}퍼센트"


def _move(v: float) -> str:
    return "올랐" if v > 0 else "내렸" if v < 0 else "보합이었"


def _num(change) -> float | None:
    t = str(change).replace("%", "").replace("bp", "").replace("−", "-").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _won(eok: float) -> str:
    return kt._eok(eok) + " 원"


def _dash(payload: dict) -> dict[str, dict]:
    return {r["id"]: r for r in payload.get("dashboard", [])}


def _sigma(payload: dict, iid: str) -> float:
    r = _dash(payload).get(iid)
    return abs(_num(r.get("sigma")) or 0) if r else 0.0


def _index_phrase(name: str, change: str) -> str | None:
    """'코스피는 3.26퍼센트 내렸' — 뒤에 '고,' 나 '어요.'를 붙인다. bp 는 퍼센트포인트로."""
    v = _num(change)
    if v is None:
        return None
    head = f"{SPOKEN.get(name, name)}{josa(name, '은/는')}"
    if v == 0:
        return f"{head} 보합이었"
    amount = f"{abs(v) / 100:.2f}퍼센트포인트" if str(change).endswith("bp") else _pct(v)
    return f"{head} {amount} {_move(v)}"


def _join(phrases: list[str | None]) -> str:
    """두 개씩 '…고, …어요.'로 잇는다."""
    ps = [p for p in phrases if p]
    return " ".join("고, ".join(ps[i:i + 2]) + "어요." for i in range(0, len(ps), 2))


# ── 구간 ────────────────────────────────────────────────────

def _market(payload: dict, ids: tuple[str, ...], tag: str, kind: str,
            extra: tuple[str, str] | None = None, priority: int = 1) -> Segment | None:
    d = _dash(payload)
    got = [i for i in ids if i in d and _num(d[i]["change"]) is not None]
    if not got:
        return None
    rows = [(NAME[i], d[i]["change"]) for i in got]
    speech = _join([_index_phrase(NAME[i], d[i]["change"]) for i in got])
    note = ""
    if extra:
        note, more = extra
        speech += " " + more
    surprise = any(_sigma(payload, i) >= SURPRISE_SIGMA for i in got)
    return Segment(tag, rows, speech, kind, note, surprise, priority)


def _breadth(payload: dict) -> tuple[str, str] | None:
    b = (((payload.get("detail") or {}).get("kr") or {}).get("breadth") or {}).get("KOSPI")
    if not b:
        return None
    return (f"코스피 오른 종목 {b['up']:,}개 · 내린 종목 {b['down']:,}개",
            f"코스피에서는 {b['up']:,}개 종목이 오르고 {b['down']:,}개가 내렸어요.")


def _verdict(payload: dict) -> Segment | None:
    head = (payload.get("verdict") or {}).get("headline")
    if not head:
        return None
    surprise = any(abs(r.get("sigma") or 0) >= SURPRISE_SIGMA
                   for r in payload.get("notable_rows", []))
    return Segment("오늘 한 줄", [], haeyo(head), "verdict", head, surprise)


def _flows(payload: dict) -> Segment | None:
    stats = {s.investor: s for s in payload.get("flow_stats", []) if s.market == "KOSPI"}
    rows, parts = [], []
    for inv, label in (("외국인합계", "외국인"), ("기관합계", "기관")):
        s = stats.get(inv)
        if not s:
            continue
        rows.append((f"{label} 코스피", f"{'+' if s.eok > 0 else '-'}{kt._eok(s.eok)}"))
        run = f" {abs(s.streak)}거래일 연속" if abs(s.streak) >= 2 else ""
        verb = "순매수했" if s.eok > 0 else "순매도했"
        parts.append(f"{label}{josa(label, '은/는')}{run} {_won(s.eok)}어치 {verb}")
    if not rows:
        return None
    speech = "코스피에서 " + _join(parts)
    note = ""
    f = ((payload.get("detail") or {}).get("kr") or {}).get("foreign") or {}
    if f.get("buy") and f.get("sell"):
        b, s = f["buy"][0], f["sell"][0]
        note = f"외국인 순매수 1위 {b['name']} · 순매도 1위 {s['name']}"
        speech += f" 외국인이 가장 많이 산 종목은 {b['name']}, 가장 많이 판 종목은 {s['name']}{_ieyo(s['name'])}."
    return Segment("누가 사고 팔았나", rows, speech, "flows", note, priority=2)


def _sectors(payload: dict) -> Segment | None:
    sec = ((payload.get("detail") or {}).get("kr") or {}).get("sectors") or []
    if len(sec) < 2:
        return None
    hi, lo = sec[0], sec[-1]
    rows = [(f"강세 {hi['name']}", f"{hi['chg_pct']:+.1f}%"),
            (f"약세 {lo['name']}", f"{lo['chg_pct']:+.1f}%")]
    speech = _join([
        f"업종 중에서는 {hi['name']}{josa(hi['name'], '이/가')} {_pct(hi['chg_pct'], 1)} {_move(hi['chg_pct'])}",
        f"{lo['name']}{josa(lo['name'], '은/는')} {_pct(lo['chg_pct'], 1)} {_move(lo['chg_pct'])}"])
    return Segment("업종", rows, speech, "sectors", priority=2)


def _stocks(payload: dict) -> Segment | None:
    kr = (payload.get("detail") or {}).get("kr") or {}
    rows, parts = [], []
    top = (kr.get("kospi_top") or [None])[0]
    if top:
        rows.append((f"시총 1위 {top['name']}", f"{top['chg_pct']:+.2f}%"))
        parts.append(f"시가총액 1위 {top['name']}{josa(top['name'], '은/는')} "
                     f"{_pct(top['chg_pct'])} {_move(top['chg_pct'])}")
    for key, label in (("gainers", "많이 오른"), ("losers", "많이 내린")):
        xs = kr.get(key) or []
        if xs:
            x = xs[0]
            rows.append((f"가장 {label} {x['name']}", f"{x['chg_pct']:+.1f}%"))
            parts.append(f"가장 {label} 종목은 {x['name']}{josa(x['name'], '으로/로')} "
                         f"{_pct(x['chg_pct'], 1)} {_move(x['chg_pct'])}")
    if not rows:
        return None
    note, speech = "", _join(parts)
    if kr.get("mover_min_eok") and len(rows) > 1:
        # 급등·급락 종목은 거래대금 하한을 넘은 종목 중에서 고른 것 — 말에서도 밝힌다
        note = f"급등·급락은 거래대금 {kr['mover_min_eok']:,.0f}억 원 이상 종목 중"
        speech = speech.replace("가장 많이 오른 종목은",
                                f"거래대금 {kr['mover_min_eok']:,.0f}억 원 넘는 종목 중 가장 많이 오른 건", 1)
        speech = speech.replace("가장 많이 내린 종목은", "가장 많이 내린 건", 1)
    return Segment("눈에 띈 종목", rows, speech, "stocks", note, priority=4)


def _us_big(payload: dict) -> Segment | None:
    big = ((payload.get("detail") or {}).get("us") or {}).get("big") or []
    if len(big) < 2:
        return None
    hi, lo = big[0], big[-1]
    rows = [(hi["name"], f"{hi['chg_pct']:+.1f}%"), (lo["name"], f"{lo['chg_pct']:+.1f}%")]
    speech = _join([
        f"미국 대형주 중에서는 {hi['name']}{josa(hi['name'], '이/가')} {_pct(hi['chg_pct'], 1)} {_move(hi['chg_pct'])}",
        f"{lo['name']}{josa(lo['name'], '은/는')} {_pct(lo['chg_pct'], 1)} {_move(lo['chg_pct'])}"])
    return Segment("미국 대형주", rows, speech, "us_big", priority=4)


def _results(payload: dict) -> Segment | None:
    done = [r for r in payload.get("results", []) if not r.get("pending") and r.get("lines")][:2]
    if not done:
        return None
    rows = [(r["title"] + (f" ({r['period']})" if r.get("period") else ""), r["lines"][0])
            for r in done]
    speech = "지난 일정 결과예요. " + " ".join(
        f"{_say(r['title'])}{(' ' + r['period']) if r.get('period') else ''}, {_say(r['lines'][0])}."
        for r in done)
    return Segment("지난 일정 결과", rows, speech, "results", priority=3)


def _events(payload: dict) -> Segment | None:
    evs = sorted([e for e in payload.get("events", []) if e.importance >= 2],
                 key=lambda e: (-e.importance, e.day))[:2]
    if not evs:
        return None
    evs.sort(key=lambda e: e.day)
    rows = [(e.when().split(" ")[0], kt._short_event(e)) for e in evs]
    last = kt._short_event(evs[-1])
    speech = "앞으로 볼 일정은 " + ", ".join(
        f"{e.day.month}월 {e.day.day}일 {_say(kt._short_event(e))}" for e in evs) + \
        _ieyo(last) + "."
    return Segment("다가오는 일정", rows, speech, "events", priority=3)


def _triggers(payload: dict) -> Segment | None:
    ts = [t for t in payload.get("triggers", [])
          if t.probability is not None and not getattr(t, "is_uncertain", False)][:2]
    if not ts:
        return None
    rows, parts = [], []
    for t in ts:
        pct = round(t.probability * 100)
        rows.append((t.name, f"{t.prob_name} {pct}%"))
        head = f"{SPOKEN.get(t.name, t.name)}{josa(t.name, '은/는')}"
        if t.kind == "round_level":
            v = t.threshold
            situ = f"{head} {v:,.0f} 가까이에 있어요." if abs(v) >= 100 else f"{head} {v:.2f} 가까이에 있어요."
        elif t.situation:
            situ = f"{head} {haeyo(t.situation)}."
        else:
            situ = ""
        parts.append(f"{situ} 비슷했던 과거로 보면 {t.prob_name}{josa(t.prob_name, '은/는')} {pct}퍼센트예요.")
    title = "이번 주 지켜볼 것" if payload.get("mode") == "weekly" else "내일 지켜볼 것"
    return Segment(title, rows, " ".join(p.strip() for p in parts), "triggers", priority=2)


def _week(payload: dict) -> Segment | None:
    week = payload.get("week")
    if not week:
        return None
    moves = {m.id: m for m in week.moves}
    got = [i for i in ("KOSPI", "KOSDAQ", "SPX", "NASDAQ") if i in moves]
    if not got:
        return None
    rows = [(NAME[i], moves[i].change_text) for i in got]
    speech = "지난주에는 " + _join([_index_phrase(NAME[i], moves[i].change_text) for i in got])
    return Segment(f"지난주 · {week.period}", rows, speech, "week")


def build(payload: dict, message: str = "", weekly: bool = False, link: str = "") -> Script | None:
    """그날 payload 로 전체 브리핑 대본을 만든다. 시장 숫자가 하나도 없으면 None."""
    body = [
        _week(payload) if weekly else None,
        _verdict(payload),
        _market(payload, ("KOSPI", "KOSDAQ"), "한국 증시", "kr", _breadth(payload)),
        _market(payload, ("SPX", "NASDAQ", "DOW"), "미국 증시", "us"),
        _market(payload, ("USDKRW", "US10Y", "WTI", "GOLD"), "환율 · 금리 · 원자재", "fx",
                priority=3),
        _flows(payload),
        _sectors(payload),
        _stocks(payload),
        _us_big(payload),
        _results(payload),
        _events(payload),
        _triggers(payload),
    ]
    segs = [s for s in body if s]
    if not any(s.kind in ("kr", "us", "week") for s in segs):
        return None

    d = date.fromisoformat(payload["brief_date"])
    title = "주간 브리핑" if weekly else "경제 브리핑"
    intro = Segment("", [], f"{d.month}월 {d.day}일 {WEEKDAY[d.weekday()]}요일 {title}이에요!",
                    "intro", priority=0)
    outro = Segment("", [], "더 자세한 건 설명란 링크에서 봐 주세요. 다음에 또 만나요!", "outro",
                    "전체 브리핑은 설명란 링크에서", priority=0)

    if weekly and payload.get("week"):
        m = next((m for m in payload["week"].moves if m.id == "KOSPI"), None)
        v = m.change if m else None
    else:
        t = _dash(payload).get("KOSPI")
        v = _num(t["change"]) if t else None
    mood = "down" if v is not None and v < 0 else "up"
    return Script(payload["date_short"], title, mood, [intro, *segs, outro], link)
