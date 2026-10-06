"""쇼츠 대본 — 주식 탭 전체 브리핑을 캐릭터가 읽는다.

동화님 결정
  9/22 23시  "그날 카톡 내용 그대로를 브리핑하듯" → 9/22 23:30 "전체 브리핑 가능하게"로 넓힘.
             말투는 아나운서처럼 또박또박하지 않아도 된다 — 캐릭터에 어울리게 해요체.

  화면     구간마다 이름표(한국 증시·환율과 금리…)와 '이름 — 값' 줄 몇 개.
  읽는 말  같은 숫자에서 만든 문장. 새 사실을 지어내지 않는다 — 모든 값은 그날 payload
           (리포트 페이지와 같은 출처)에서 온다. 값이 없는 구간은 통째로 뺀다.
  길이     쇼츠는 3분까지. 우선순위(priority, 클수록 덜 중요)가 큰 구간부터 빼서
           render 가 MAX_SEC 안에 맞춘다.

  덧붙이는 말  화면 카드에 없는 이야기도 하차니가 한다 (9/23 동화님: "위에 뜬 글에 없는 내용도
             더 많이 얘기하게, 이유라던가 그 종목의 이슈 한 가지라던가"). 다만 '이유'를 단정하지
             않는다 — 이미 모은 근거만 말한다:
               · 급등·급락 종목의 네이버 뉴스 제목 / DART 공시 제목 (detail.kr.issues)
               · 지수가 평소 하루 변동폭의 몇 배 움직였는지(σ), 지난 1년 중 어느 높이인지(52주 위치)
             뉴스는 "관련 기사 제목은 '…'" 처럼 제목을 그대로 읽는다. 기사가 원인이라고 말하지 않는다.

  휴장       (9/27 동화님) 한국장이 쉰 다음 날은 한국 부분을 되풀이하지 않고 미국 소식만,
             미국장이 쉰 다음 날은 한국 소식만 — 어느 장이 왜 쉬었는지 말한다. 둘 다 쉬면 영상 없음.
             장이 쉬기 전 날(과 쉬는 당일)에는 끝에 '내일은 ○○로 한국/미국 증시가 쉬어요'를 덧붙인다.
             쉬었다는 판단은 달력(brief/market_calendar.py)과 실제 데이터 기준일이 둘 다 맞을 때만.

  첫 장면     (9/27 동화님) 날짜 인사보다 그날 가장 센 숫자부터 — '오늘 가장 큰 뉴스'(hook).
             고르는 규칙: 평소 하루 변동폭의 1.5배 넘게 움직인 지표나 외국인 매매가 있으면 그중
             가장 큰 것, 없으면 가장 많이 오른 종목(+10% 이상), 그것도 없으면 코스피.
             영상 제목도 이 한 줄로 시작한다. 썸네일은 그대로 '오늘 이야기할 것' 장면.

  길이       (9/29 동화님: "너무 길다, 쓸데없는 문장 빼고 중요한 것 위주로") 1분 남짓을 목표로:
             '평소 범위 안이었다' 같은 안 중요한 맥락, 오늘 한 줄(첫 장면과 겹침), 오른·내린 종목 수,
             외국인 1위 종목 이름, 시총 1위, 급등 종목 하나 더, 미국 대형주는 말하지 않는다(카드에는
             남는 것도 있다). 환율·금리·원자재는 원/달러 + 크게 움직인 것만, 지켜볼 조건은 하나.

  자켓 색    코스피가 오른 날(주간은 지난주 코스피가 오른 주) 빨강, 내린 날 파랑.
  놀람 표정  그 구간 지표가 '평소 하루 변동폭의 SURPRISE_SIGMA 배' 이상 움직였을 때만.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

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
    headline: str = ""          # 영상 제목 앞머리 — 첫 장면(hook)의 한 줄

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


# ── 카드에 없는 덧붙이는 말 ────────────────────────────────

# 내용 없이 궁금증만 부르는 제목 — 읽어 줘도 정보가 없다
GENERIC_HEADLINE = re.compile(r"(급등세|급락세|상승세|하락세|강세|약세)\s*[.…·]{1,3}\s*(왜|이유)|왜\s*\?|이유는\s*\?|무슨 회사"
                              r"|등\s*마감|등\s*상한가|등\s*\d+개|특징주\s*모음")


def _clean_headline(t: str) -> str:
    t = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", t)               # [속보] [특징주] 같은 머리표
    for a, b in (("株", "주"), ("美", "미국 "), ("中", "중국 "), ("日", "일본 "), ("韓", "한국 "), ("北", "북한 ")):
        t = t.replace(a, b)
    t = re.sub(r"\s*(…|⋯|\.\.\.+|···)\s*", ", ", t)
    t = re.sub(r"[\"'‘’“”?!]", "", t)                        # 따옴표·물음표는 문장 나누기를 흐린다
    return re.sub(r"\s+", " ", t).strip(" ,")


def _issue(payload: dict, name: str) -> str | None:
    """그 종목의 뉴스 제목 한 줄, 없으면 공시 제목 한 줄."""
    for i in ((payload.get("detail") or {}).get("kr") or {}).get("issues") or []:
        if i.get("name") != name:
            continue
        heads = [n["title"] for n in i.get("news", []) if not GENERIC_HEADLINE.search(n["title"])]
        if heads:
            h = _clean_headline(heads[0])
            return f"기사 제목은 '{h}'{josa(h, '이었/였')}어요."
        if i.get("disclosures"):
            d = _clean_headline(i["disclosures"][0]["title"])
            return f"공시로는 '{d}'{josa(d.rstrip(')'), '이/가')} 올라왔어요."
    return None


def _extra_mover(payload: dict, skip: set[str]) -> str | None:
    """1위 말고도 기사 제목이 뚜렷한 급등 종목 하나."""
    for i in ((payload.get("detail") or {}).get("kr") or {}).get("issues") or []:
        if i.get("side") != "up" or i.get("name") in skip:
            continue
        heads = [n["title"] for n in i.get("news", []) if not GENERIC_HEADLINE.search(n["title"])]
        if heads:
            h = _clean_headline(heads[0])
            return (f"{i['name']}도 {_pct(i['chg_pct'], 1)} 올랐는데, "
                    f"'{h}'{josa(h, '이라는/라는')} 기사가 있었어요.")
    return None


def _context(payload: dict, iid: str, name: str, sigma: bool = True) -> str:
    """평소보다 얼마나 움직였는지(σ), 1년 중 어느 높이인지 — 대시보드 값 그대로."""
    r = _dash(payload).get(iid)
    if not r:
        return ""
    s, p52 = _num(r.get("sigma")), _num(r.get("p52"))
    out = []
    head = f"{SPOKEN.get(name, name)}{josa(name, '은/는')}"
    if sigma and s is not None:
        a = abs(s)
        if a >= SURPRISE_SIGMA:                    # 짧게 — 크게 움직였을 때만 말한다
            out.append(f"{head} 평소 하루 변동폭의 {a:.1f}배나 움직였어요.")
            head = "지금은"
    if p52 is not None and p52 >= 95:
        out.append(f"{head} 지난 1년 중 가장 높은 수준 근처예요.")
    elif p52 is not None and p52 <= 5:
        out.append(f"{head} 지난 1년 중 가장 낮은 수준 근처예요.")
    return " ".join(out)


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
        note, _more = extra                        # 말로는 하지 않고 카드에만 (짧게)
    # 카드에 없는 말 — 대표 지표 하나는 σ·52주 위치, 나머지는 1년 최고·최저일 때만
    lead, *rest = got
    ctx = [_context(payload, lead, NAME[lead])] +           ([_context(payload, i, NAME[i], sigma=False) for i in rest] if kind == "fx" else [])
    speech += "".join(" " + c for c in ctx if c)
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
    return Segment("업종", rows, speech, "sectors", priority=3)


def _stocks(payload: dict) -> Segment | None:
    kr = (payload.get("detail") or {}).get("kr") or {}
    rows, said = [], []
    top = (kr.get("kospi_top") or [None])[0]
    if top:                                        # 시총 1위는 카드에만
        rows.append((f"시총 1위 {top['name']}", f"{top['chg_pct']:+.2f}%"))
    floor = kr.get("mover_min_eok")
    names = {x["name"] for key in ("gainers", "losers") for x in (kr.get(key) or [])[:1]}
    has_news = False
    for key, label in (("gainers", "많이 오른"), ("losers", "많이 내린")):
        xs = kr.get(key) or []
        if not xs:
            continue
        x = xs[0]
        rows.append((f"가장 {label} {x['name']}", f"{x['chg_pct']:+.1f}%"))
        # 급등·급락은 거래대금 하한을 넘은 종목 중에서 고른 것 — 처음 한 번 말에서도 밝힌다
        lead = (f"거래대금 {floor:,.0f}억 넘는 종목 중 " if floor and key == "gainers" else "")
        said.append(f"{lead}가장 {label} 건 {x['name']}{josa(x['name'], '으로/로')} "
                    f"{_pct(x['chg_pct'], 1)} {_move(x['chg_pct'])}어요.")
        if _issue(payload, x["name"]):
            has_news = True

    if not rows:
        return None
    # 기사는 하나씩 읽지 않고 브리핑 페이지로 안내한다 (9/29 동화님). 쇼츠 설명란 링크는
    # 눌리지 않아서(유튜브 정책) 주소를 영상 설명에 적어 두고 복사해 들어가게 한다.
    # 브리핑 웹사이트는 지인·구독자 전용이라 영상에서 알리지 않는다 (9/29 동화님).
    if has_news:
        said.append("두 종목 관련 기사 주소는 영상 설명에 적어 뒀으니 확인해 보세요.")
    note = f"급등·급락은 거래대금 {floor:,.0f}억 원 이상 종목 중" if floor and len(rows) > 1 else ""
    return Segment("눈에 띈 종목", rows, " ".join(said), "stocks", note, priority=1)


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
          if t.probability is not None and not getattr(t, "is_uncertain", False)][:1]
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
        parts.append(f"{situ} {t.prob_name}{josa(t.prob_name, '은/는')} {pct}퍼센트예요.")
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


# ── 휴장 ────────────────────────────────────────────────────

KR_IDS = {"KOSPI", "KOSDAQ", "KTB3Y", "KR_BASE"}
US_IDS = {"SPX", "NASDAQ", "DOW", "RUSSELL2000", "VIX", "US10Y"}
KR_WORDS = ("코스피", "코스닥", "국고채", "한국 기준금리")
US_WORDS = ("S&P", "나스닥", "다우", "러셀", "VIX", "미 국채", "미국 10년물")


def _eul(word: str) -> str:
    return "으로" if word and _batchim(word[-1]) and (ord(word[-1]) - 0xAC00) % 28 != 8 else "로"


def market_status(payload: dict, weekly: bool = False) -> dict:
    """어제(직전 평일) 한국·미국 장이 열렸는지, 앞으로 쉬는 날은 언제인지.

    '쉬었다'는 달력이 휴장이라 하고 실제 데이터 기준일도 그날보다 앞일 때만 — 둘 중 하나만이면
    쉬었다고 말하지 않는다 (달력에 없는 임시 휴장, 또는 수집 실패와 헷갈리지 않게).
    """
    from brief import market_calendar as mc
    d = date.fromisoformat(payload["brief_date"])
    out = {"kr_fresh": True, "us_fresh": True, "kr_reason": None, "us_reason": None, "ahead": []}
    if not weekly:
        prev = mc.prev_weekday(d)
        for m, key in (("KR", "kr"), ("US", "us")):
            name = mc.holiday_name(m, prev)
            got = payload.get(f"{key}_date") or ""
            if name and got and got < prev.isoformat():
                out[f"{key}_fresh"], out[f"{key}_reason"] = False, name
    # 앞으로 — 오늘이 휴장이면 오늘부터, 아니면 다음 평일부터 이어지는 휴장
    for m in ("KR", "US"):
        start = d if (d.weekday() < 5 and mc.holiday_name(m, d)) else mc.next_weekday(d)
        run = mc.closed_run(m, start)
        if run:
            out["ahead"].append({"market": m, "label": mc.label(m), "days": run,
                                 "name": mc.holiday_name(m, run[0])})
    return out


def _closed_now(st: dict) -> Segment | None:
    """어제 한쪽 장이 쉬었을 때 — 브리핑 앞머리 안내."""
    for key, other in (("kr", "미국"), ("us", "한국")):
        reason = st[f"{key}_reason"]
        if st[f"{key}_fresh"] or not reason:
            continue
        label = "한국 증시" if key == "kr" else "미국 증시"
        speech = (f"어제는 {reason}{_eul(reason)} {label}가 쉬었어요. "
                  f"그래서 오늘은 {other} 증시 소식을 중심으로 전해 드릴게요.")
        return Segment("휴장 안내", [(label, f"휴장 · {reason}")], speech, "closed", priority=0)
    return None


def _ahead(st: dict, today: date) -> Segment | None:
    """쉬기 전 날(과 쉬는 날) — 끝에 덧붙이는 안내."""
    if not st["ahead"]:
        return None

    def when(d: date) -> str:
        if d == today:
            return "오늘"
        if d == today + timedelta(days=1):
            return "내일"
        return f"{d.month}월 {d.day}일 {WEEKDAY[d.weekday()]}요일"

    items = st["ahead"]
    same = len(items) == 2 and items[0]["days"] == items[1]["days"]
    # 두 장이 같은 날 쉬면 한 문장으로 (이름은 한국 쪽 — 성탄절/크리스마스처럼 같은 날이 대부분)
    groups = ([(items[0]["days"], "한국과 미국 증시가 모두", items[0]["name"])] if same
              else [(it["days"], f"{it['label']}는", it["name"]) for it in items])
    rows, said = [], []
    for days, who, name in groups:
        first, last = days[0], days[-1]
        span = f"{when(first)}부터 {last.month}월 {last.day}일까지" if len(days) > 1 else f"{when(first)}은"
        said.append(f"{span} {name}{_eul(name)} {who} 쉬어요.")
        dates = f"{first.month}/{first.day}" + (f"~{last.month}/{last.day}" if len(days) > 1 else "")
        rows.append(("한국·미국 증시" if same else who.replace("는", ""), f"{dates} {name}"))
    # 오늘 두 장이 모두 쉬면 내일 아침엔 새 소식이 없다 — 영상도 쉰다
    both_today = all(it["days"][0] == today for it in items) and len(items) == 2
    if both_today and today.weekday() < 5:
        said.append("그래서 내일은 영상을 쉬어 갈게요.")
    return Segment("쉬어가는 날", rows, "참고로 " + " ".join(said), "ahead", priority=1)


# ── 첫 장면: 오늘 가장 큰 뉴스 ─────────────────────────────

HOOK_SIGMA = 1.5          # 리포트의 '오늘 볼 것' 기준과 같다
HOOK_STOCK_PCT = 10.0


def _hook(payload: dict, kr: bool, us: bool) -> tuple[Segment, str, str] | None:
    """(장면, 영상 제목용 한 줄, 겹치면 뺄 결론 속 이름). 숫자는 모두 payload 그대로."""
    dash = _dash(payload)
    stale = (KR_IDS if not kr else set()) | (US_IDS if not us else set())
    best = None                                    # (σ, 종류, 값)
    for iid, name in NAME.items():
        r = dash.get(iid)
        if not r or iid in stale or _num(r.get("change")) is None:
            continue
        sg = _num(r.get("sigma"))
        if sg is not None and (best is None or abs(sg) > best[0]):
            best = (abs(sg), "row", (iid, name, r["change"], sg))
    if kr:
        for f in payload.get("flow_stats", []):
            if f.market == "KOSPI" and f.investor == "외국인합계" and f.sigma is not None:
                if best is None or abs(f.sigma) > best[0]:
                    best = (abs(f.sigma), "flow", f)

    if best and best[0] >= HOOK_SIGMA:
        if best[1] == "row":
            iid, name, change, sg = best[2]
            v = _num(change)
            amount = f"{abs(v) / 100:.2f}퍼센트포인트" if change.endswith("bp") else _pct(v)
            spoken = SPOKEN.get(name, name)
            speech = (f"{spoken}{josa(name, '이/가')} 하루 만에 {amount}{'나' if best[0] >= SURPRISE_SIGMA else ''} "
                      f"{_move(v)}어요! 평소 하루 변동폭의 {best[0]:.1f}배예요.")
            seg = Segment("오늘 가장 큰 뉴스", [(name, change)], speech, "hook",
                          f"평소 하루 변동폭의 {best[0]:.1f}배", best[0] >= SURPRISE_SIGMA, 0)
            return seg, f"{name} {change}, 평소 변동폭의 {best[0]:.1f}배", name
        f = best[2]
        verb, word = ("샀", "순매수") if f.eok > 0 else ("팔았", "순매도")
        kospi = dash.get("KOSPI")
        kv = _num(kospi["change"]) if kospi else None
        speech = f"외국인이 코스피에서 {_won(f.eok)}어치를 {verb}어요!"
        title = f"외국인 {kt._eok(f.eok)} {word}"
        if kv is not None:
            opposite = (kv > 0) != (f.eok > 0) and kv != 0
            speech += f" {'그런데 ' if opposite else ''}코스피는 {_pct(kv)} {_move(kv)}어요."
            title += f"{'에도' if opposite else ','} 코스피 {kospi['change']}"
        seg = Segment("오늘 가장 큰 뉴스", [("외국인 코스피", f"{'+' if f.eok > 0 else '-'}{kt._eok(f.eok)}")],
                      speech, "hook", f"평소 하루 매매 규모의 {best[0]:.1f}배", best[0] >= SURPRISE_SIGMA, 0)
        return seg, title, "외국인"

    kr_d = (payload.get("detail") or {}).get("kr") or {}
    top = (kr_d.get("gainers") or [None])[0] if kr else None
    if top and top["chg_pct"] >= HOOK_STOCK_PCT:
        speech = f"오늘 가장 많이 오른 종목은 {top['name']}, 무려 {_pct(top['chg_pct'], 1)} 올랐어요!"
        seg = Segment("오늘 가장 큰 뉴스", [(top["name"], f"{top['chg_pct']:+.2f}%")], speech, "hook",
                      "오늘 가장 많이 오른 종목", top["chg_pct"] >= 15, 0)
        return seg, f"{top['name']} {top['chg_pct']:+.2f}%, 오늘 가장 많이 오른 종목", top["name"]

    # 조용한 날(크게 움직인 것도 +10% 종목도 없음)은 첫 장면을 따로 두지 않는다 —
    # 지수로 시작하면 바로 뒤 '한국/미국 증시'에서 같은 말을 되풀이하게 된다.
    return None


def _macro_ref(payload: dict, kr: bool) -> Segment | None:
    """쇼츠 끝 한마디 — 핵심 거시 이슈의 '과거 데이터로 본 참고' (9/29 동화님).
    행동을 권하지 않고 과거 통계와 '평소' 비율만 말한다."""
    want = "코스피" if kr else "S&P 500"
    for m in payload.get("macro_view") or []:
        st = next((x for x in m.get("stats", []) if x["target"] == want), None)
        if not st or not st.get("base"):
            continue
        spoken = want.replace("S&P 500", "S&P 500")
        speech = (f"참고로 과거에 {m['cond']}, 한 달 뒤까지 확인된 {st['n']}번 중 "
                  f"{spoken}{josa(want, '이/가')} 오른 경우가 {st['up']}퍼센트였어요. "
                  f"평소엔 {st['base']['up']}퍼센트예요. 참고하시면 좋을 것 같아요!")
        rows = [(f"한 달 뒤 {want} 오른 비율", f"{st['up']}%"), ("평소(모든 날)", f"{st['base']['up']}%")]
        return Segment("과거 데이터로 본 참고", rows, speech, "macro", m["cond"], priority=1)
    return None


# ── '오늘의 숫자' — 하루에 숫자 하나만 (10/2 동화님) ─────────────────
#
# 조회수: 하차니 유머 영상은 1,000회 안팎, 1~2분 전체 브리핑은 대부분 100회 미만이고
# 9/28 영상은 쇼츠 피드 유입 0%, 계속 시청 37% (스튜디오, 10/2). 쇼츠는 첫 2초에 넘겨지면 끝이라
# 그날 가장 센 사실 하나만 20~30초에 말한다. 자극적이어도 숫자는 모두 payload 그대로이고,
# '급등·급락'은 평소 하루 변동폭의 SURPRISE_SIGMA 배를 넘을 때만 쓴다. 원인은 지어내지 않는다.

ONE_STOCK_PCT = 5.0       # 조용한 날 — 이만큼 넘게 오른 종목이 있으면 그 종목 이야기
ONE_STOCK_BIG = 10.0      # 급등 종목(이만큼 이상)이 있으면 무엇보다 먼저 (10/2 동화님: "급등을 먼저")
ONE_NAME = {**NAME, "DXY": "달러인덱스"}


def _p52_label(p: float) -> str:
    return f"상위 {max(1, round(100 - p))}%" if p >= 50 else f"하위 {max(1, round(p))}%"


def _one_row(payload: dict, iid: str, name: str, change: str, sg: float):
    """지표 하나가 크게 움직인 날 — (첫 장면, 왜 큰 일인지, 제목)."""
    v = _num(change)
    big = abs(sg) >= SURPRISE_SIGMA
    amount = f"{abs(v) / 100:.2f}퍼센트포인트" if change.endswith("bp") else _pct(v)
    spoken = SPOKEN.get(name, name)
    word = ("급등" if v > 0 else "급락") if big else ("상승" if v > 0 else "하락")
    hook = Segment("오늘의 숫자", [(name, change)],
                   f"{spoken}{josa(name, '이/가')} 하루 만에 {amount}{'나' if big else ''} {_move(v)}어요!",
                   "hook", f"평소 하루 변동폭의 {abs(sg):.1f}배", big, 0)
    rows = [("평소 하루 변동폭의", f"{abs(sg):.1f}배")]
    said = [f"평소 하루 움직임의 {abs(sg):.1f}배예요."]
    p52 = _num((_dash(payload).get(iid) or {}).get("p52"))
    if p52 is not None:
        rows.append(("지난 1년 중 높이", _p52_label(p52)))
        if p52 >= 95:
            said.append("지난 1년 중 가장 높은 수준 근처까지 왔어요.")
        elif p52 <= 5:
            said.append("지난 1년 중 가장 낮은 수준 근처까지 왔어요.")
    why = Segment("얼마나 큰 일이냐면", rows, " ".join(said), "why", "", False, 1)
    title = f"{name} 하루 {change} {word}, 평소의 {abs(sg):.1f}배"
    if p52 is not None and (p52 >= 95 or p52 <= 5):
        title += f" · 1년 중 {'최고' if p52 >= 95 else '최저'} 근처"
    return hook, why, title


def _one_flow(payload: dict, f):
    """외국인 매매가 평소보다 컸던 날."""
    verb, word = ("샀", "순매수") if f.eok > 0 else ("팔았", "순매도")
    big = abs(f.sigma) >= SURPRISE_SIGMA
    hook = Segment("오늘의 숫자", [("외국인 코스피", f"{'+' if f.eok > 0 else '-'}{kt._eok(f.eok)}")],
                   f"외국인이 코스피에서 하루에 {_won(f.eok)}어치를 {verb}어요!", "hook",
                   f"평소 하루 매매 규모의 {abs(f.sigma):.1f}배", big, 0)
    rows = [("평소 하루 매매 규모의", f"{abs(f.sigma):.1f}배")]
    said = [f"평소 외국인 하루 매매 규모의 {abs(f.sigma):.1f}배예요."]
    if abs(f.streak) >= 2:
        rows.append(("연속", f"{abs(f.streak)}거래일 {word}"))
        said.append(f"벌써 {abs(f.streak)}거래일 연속 {word}예요.")
    kospi = _dash(payload).get("KOSPI")
    kv = _num(kospi["change"]) if kospi else None
    if kv is not None:
        rows.append(("코스피", kospi["change"]))
        opposite = (kv > 0) != (f.eok > 0) and kv != 0
        said.append(f"{'그런데 ' if opposite else ''}코스피는 {_pct(kv)} {_move(kv)}어요.")
    why = Segment("얼마나 큰 일이냐면", rows, " ".join(said), "why", "", False, 1)
    title = f"외국인 하루 {kt._eok(f.eok)} {word}, 평소의 {abs(f.sigma):.1f}배"
    return hook, why, title


def _one_stock(payload: dict, x: dict):
    """종목 하나가 크게 오른 날 — 기사 제목은 그대로 읽고 원인이라고 말하지 않는다."""
    pct = x["chg_pct"]
    # 상한가는 보통 날의 가격제한폭(+30%)에 닿았을 때만 — 상장 첫날(공모가 대비 최대 +300%)이나
    # 제한폭이 다른 경우를 상한가라고 하지 않는다 (10/2 브릴스 +56% 는 상한가가 아니었다)
    limit = 29.5 <= pct <= 30.05
    hook = Segment("오늘의 숫자", [(x["name"], f"{pct:+.2f}%")],
                   f"{x['name']}{josa(x['name'], '이/가')} 하루 만에 {_pct(pct, 1)} 올랐어요!"
                   + (" 상한가예요." if limit else ""), "hook",
                   "상한가" if limit else "오늘 가장 많이 오른 종목", pct >= 15, 0)
    news = _issue(payload, x["name"])
    kospi = _dash(payload).get("KOSPI")
    rows = [("오늘 코스피", kospi["change"])] if kospi else []
    said = []
    if news:
        said.append("관련 " + news + " 기사 주소는 영상 설명에 적어 뒀어요.")
    if kospi and _num(kospi["change"]) is not None:
        kv = _num(kospi["change"])
        said.append(f"같은 날 코스피는 {_pct(kv)} {_move(kv)}어요.")
    why = Segment("무슨 일이냐면", rows, " ".join(said), "why", "", False, 1) if said else None
    title = f"{x['name']} 하루 {pct:+.1f}%" + (" 상한가" if limit else " 급등" if pct >= ONE_STOCK_BIG else "")
    return hook, why, title


def _one_past(payload: dict, iid: str, kr: bool) -> Segment | None:
    """그 지표가 '과거엔 어땠나' 통계에 있으면 — 같은 조건 뒤 한 달 코스피(또는 S&P 500)."""
    want = "코스피" if kr else "S&P 500"
    for m in payload.get("macro_view") or []:
        if m.get("id") != iid:
            continue
        st = next((x for x in m.get("stats", []) if x["target"] == want), None)
        if not st or not st.get("base"):
            return None
        speech = (f"과거에 {m['cond']}이 {st['n']}번 있었는데, 한 달 뒤 {want}{josa(want, '이/가')} 오른 경우가 "
                  f"{st['up']}퍼센트였어요. 평소엔 {st['base']['up']}퍼센트예요. 참고하시면 좋을 것 같아요!")
        rows = [(f"한 달 뒤 {want} 오른 비율", f"{st['up']}%"), ("평소(모든 날)", f"{st['base']['up']}%")]
        return Segment("과거엔 어땠나", rows, speech, "macro", m["cond"], priority=1)
    return None


# ── 10/6 동화님: "확률보다 어떤 종목이 오르고 내렸는지 중심으로. 확률이 많이 높을 때만 확률로.
#    오늘 영상은 무슨 얘기를 하고 싶은지 모르겠다" → 오늘 무슨 일이 있었나를 종목으로 보여 준다.
STOCK_BIG = 10.0          # 이만큼 넘게 움직인 종목은 '급등·급락'
SKEW_PP = 15              # '과거엔 어땠나'는 오른 비율이 평소와 이만큼(%p) 넘게 다를 때만


def _headline(payload: dict, name: str) -> str | None:
    """그 종목의 기사 제목(없으면 공시 제목) 한 줄 — 손대지 않고 정리만."""
    for i in ((payload.get("detail") or {}).get("kr") or {}).get("issues") or []:
        if i.get("name") != name:
            continue
        heads = [n["title"] for n in i.get("news", []) if not GENERIC_HEADLINE.search(n["title"])]
        # [특징주] 기사는 그날 왜 움직였는지를 다루는 경우가 많아 먼저 (제목 그대로 읽는다)
        heads.sort(key=lambda t: "특징주" not in t)
        if heads:
            return _clean_headline(heads[0])
        if i.get("disclosures"):
            return _clean_headline(i["disclosures"][0]["title"])
    return None


def _stock_word(pct: float) -> str:
    if 29.5 <= pct <= 30.05:
        return "상한가"
    if -30.05 <= pct <= -29.5:
        return "하한가"
    if abs(pct) >= STOCK_BIG:
        return "급등" if pct > 0 else "급락"
    return "상승" if pct > 0 else "하락"


def _movers(payload: dict, kr: bool, us: bool):
    """(가장 크게 움직인 종목, 반대 방향으로 가장 크게 움직인 종목, 출처 'kr'/'us')."""
    if kr:
        kd = (payload.get("detail") or {}).get("kr") or {}
        g, l = (kd.get("gainers") or [None])[0], (kd.get("losers") or [None])[0]
        if g or l:
            lead, other = (g, l) if (g and (not l or abs(g["chg_pct"]) >= abs(l["chg_pct"]))) else (l, g)
            return lead, other, "kr"
    if us:
        big = [x for x in ((payload.get("detail") or {}).get("us") or {}).get("big") or []
               if x.get("chg_pct") is not None]
        if big:
            lead = max(big, key=lambda x: abs(x["chg_pct"]))
            opp = [x for x in big if (x["chg_pct"] > 0) != (lead["chg_pct"] > 0) and x["chg_pct"] != 0]
            return lead, (max(opp, key=lambda x: abs(x["chg_pct"])) if opp else None), "us"
    return None, None, ""


def _market_line(payload: dict, side: str, stale: set) -> Segment | None:
    """오늘 시장 한 줄 — 지수 둘, 그리고 평소의 2배 넘게 움직인 환율·금리·원자재가 있으면 하나."""
    dash = _dash(payload)
    ids = ("KOSPI", "KOSDAQ") if side == "kr" else ("SPX", "NASDAQ")
    got = [i for i in ids if i in dash and _num(dash[i]["change"]) is not None]
    if not got:
        return None
    rows = [(NAME[i], dash[i]["change"]) for i in got]
    speech = _join([_index_phrase(NAME[i], dash[i]["change"]) for i in got])
    big = None
    for iid in ("USDKRW", "DXY", "US10Y", "KTB3Y", "WTI", "GOLD", "VIX"):
        r = dash.get(iid)
        sg = _num((r or {}).get("sigma"))
        if r and iid not in stale and sg is not None and abs(sg) >= SURPRISE_SIGMA and (big is None or abs(sg) > big[1]):
            big = (iid, abs(sg))
    if big:
        iid, sg = big
        name = ONE_NAME[iid]
        ch = dash[iid]["change"]
        v = _num(ch)
        amount = f"{abs(v) / 100:.2f}퍼센트포인트" if ch.endswith("bp") else _pct(v)
        rows.append((name, ch))
        speech += (f" 그리고 {SPOKEN.get(name, name)}{josa(name, '이/가')} {amount} {_move(v)}는데, "
                   f"평소 하루 움직임의 {sg:.1f}배였어요.")
    return Segment("오늘 시장은", rows, speech, "market", "", False, 1), (big[0] if big else None)


def build_one(payload: dict, weekly: bool = False) -> Script | None:
    """'오늘의 숫자' — 오늘 가장 크게 움직인 종목 이야기 20~35초. 시장 숫자가 없으면 None."""
    st = market_status(payload, weekly)
    kr, us = st["kr_fresh"], st["us_fresh"]
    if not (kr or us):
        return None
    lead, other, side = _movers(payload, kr, us)
    if not lead:
        return _build_one_macro(payload, weekly)
    stale = (KR_IDS if not kr else set()) | (US_IDS if not us else set())
    dash = _dash(payload)

    pct = lead["chg_pct"]
    word = _stock_word(pct)
    where = "" if side == "kr" else "미국 대형주 중 "
    hook = Segment("오늘의 숫자", [(lead["name"], f"{pct:+.2f}%")],
                   f"{where}{lead['name']}{josa(lead['name'], '이/가')} 하루 만에 {_pct(pct, 1)}"
                   f"{'나' if abs(pct) >= STOCK_BIG else ''} {_move(pct)}어요!"
                   + (f" {word}예요." if word in ("상한가", "하한가") else ""), "hook",
                   ("오늘 가장 많이 " + ("오른" if pct > 0 else "내린") + " 종목") if side == "kr"
                   else "미국 대형주 중 가장 크게 움직인 종목", abs(pct) >= STOCK_BIG, 0)
    segs = [hook]
    head = _headline(payload, lead["name"]) if side == "kr" else None
    if head:                                            # 기사 제목은 그대로 — 원인이라고 말하지 않는다
        segs.append(Segment("무슨 일이?", [], f"관련 기사 제목은 '{head}'{josa(head, '이었/였')}어요.",
                            "news", f"관련 기사: {head}", False, 1))
    if other:
        p2 = other["chg_pct"]
        segs.append(Segment("반대로", [(other["name"], f"{p2:+.2f}%")],
                            f"반대로 {other['name']}{josa(other['name'], '은/는')} {_pct(p2, 1)} {_move(p2)}어요.",
                            "other", "", False, 1))
    mk = _market_line(payload, side, stale)
    big_iid = None
    if mk:
        seg, big_iid = mk
        segs.append(seg)
    # 과거 통계 — 평소와 많이 다를 때만 (10/6 동화님)
    past = _one_past(payload, big_iid, kr) if big_iid else None
    if past:
        a, b = (_num(past.rows[0][1]) or 0), (_num(past.rows[1][1]) or 0)
        if abs(a - b) < SKEW_PP:
            past = None
    d = date.fromisoformat(payload["brief_date"])
    ahead = _ahead(st, d)
    if ahead and past:
        ahead.speech = ahead.speech.replace("참고로 ", "그리고 ", 1)
    closed = _closed_now(st)
    outro = Segment("", [], "오늘의 숫자였어요. 내일 또 만나요!", "outro", "오늘의 숫자 끝", priority=0)
    segs = [segs[0], *([closed] if closed else []), *segs[1:], *(s for s in (past, ahead, outro) if s)]

    title = f"{lead['name']} {pct:+.1f}% {word}"
    if other:
        title += f", {other['name']} {other['chg_pct']:+.1f}%"
    v = _num((dash.get("KOSPI" if kr else "SPX") or {}).get("change"))
    mood = "down" if v is not None and v < 0 else "up"
    return Script(payload["date_short"], "오늘의 숫자", mood, segs, "", title)


def _build_one_macro(payload: dict, weekly: bool = False) -> Script | None:
    """(예비) 종목 자료가 없는 날 — 가장 크게 움직인 지표 하나로."""
    st = market_status(payload, weekly)
    kr, us = st["kr_fresh"], st["us_fresh"]
    if not (kr or us):
        return None
    dash = _dash(payload)
    stale = (KR_IDS if not kr else set()) | (US_IDS if not us else set())
    best = None                                        # (|σ|, 종류, 값)
    for iid, name in ONE_NAME.items():
        r = dash.get(iid)
        if not r or iid in stale or _num(r.get("change")) is None or _num(r.get("sigma")) is None:
            continue
        sg = _num(r["sigma"])
        if best is None or abs(sg) > best[0]:
            best = (abs(sg), "row", (iid, name, r["change"], sg))
    if kr:
        for f in payload.get("flow_stats", []):
            if f.market == "KOSPI" and f.investor == "외국인합계" and f.sigma is not None:
                if best is None or abs(f.sigma) > best[0]:
                    best = (abs(f.sigma), "flow", f)

    kr_d = (payload.get("detail") or {}).get("kr") or {}
    top = (kr_d.get("gainers") or [None])[0] if kr else None
    past, iid = None, None
    # 순서: 급등 종목(+10%↑) > 평소의 1.5배 넘게 움직인 지표·외국인 > +5% 종목 > 가장 많이 움직인 지표
    if top and top["chg_pct"] >= ONE_STOCK_BIG:
        hook, why, title = _one_stock(payload, top)
    elif best and best[0] >= HOOK_SIGMA:
        if best[1] == "row":
            iid = best[2][0]
            hook, why, title = _one_row(payload, *best[2])
        else:
            iid = "FOREIGN"
            hook, why, title = _one_flow(payload, best[2])
        past = _one_past(payload, iid, kr)
    elif top and top["chg_pct"] >= ONE_STOCK_PCT:
        hook, why, title = _one_stock(payload, top)
    elif best and best[1] == "row":                     # 아주 조용한 날 — 그래도 가장 많이 움직인 지표
        iid = best[2][0]
        hook, why, title = _one_row(payload, *best[2])
        past = _one_past(payload, iid, kr)
    else:
        return None

    d = date.fromisoformat(payload["brief_date"])
    v = _num((dash.get("KOSPI" if kr else "SPX") or {}).get("change"))
    mood = "down" if v is not None and v < 0 else "up"
    outro = Segment("", [], "오늘의 숫자였어요. 내일 또 만나요!", "outro", "오늘의 숫자 끝", priority=0)
    ahead = _ahead(st, d)
    if ahead and past:                                  # '참고로 …' 와 '참고하시면 …' 이 겹치지 않게
        ahead.speech = ahead.speech.replace("참고로 ", "그리고 ", 1)
    segs = [s for s in (_closed_now(st), hook, why, past, ahead, outro) if s]
    if segs[0].kind == "closed":                        # 휴장 안내보다 숫자가 먼저 — 첫 2초가 전부다
        segs[0], segs[1] = segs[1], segs[0]
    return Script(payload["date_short"], "오늘의 숫자", mood, segs, "", title)


def build(payload: dict, message: str = "", weekly: bool = False, link: str = "") -> Script | None:
    """그날 payload 로 전체 브리핑 대본을 만든다. 시장 숫자가 하나도 없으면 None."""
    st = market_status(payload, weekly)
    kr, us = st["kr_fresh"], st["us_fresh"]
    if not (kr or us):
        return None                                   # 두 장 모두 쉰 다음 날 — 영상 없음
    fx_ids = ("USDKRW", "US10Y", "WTI", "GOLD") if us else ("USDKRW", "WTI", "GOLD")
    # 원/달러는 늘, 나머지는 평소보다 크게(1.5σ) 움직였을 때만 (9/29 짧게)
    fx_ids = tuple(i for i in fx_ids if i == "USDKRW" or _sigma(payload, i) >= HOOK_SIGMA)
    verdict = _verdict(payload)
    if verdict and ((not kr and any(w in verdict.note for w in KR_WORDS))
                    or (not us and any(w in verdict.note for w in US_WORDS))):
        verdict = None                                # 쉰 장의 옛 숫자로 만든 결론은 읽지 않는다
    hooked = None if weekly else _hook(payload, kr, us)
    if hooked and verdict and hooked[2] in verdict.note:
        verdict = None                                # 첫 장면과 같은 이야기면 결론은 되풀이하지 않는다
    if hooked:                                        # 첫 장면 지표는 환율 칸·지켜볼 조건에서 다시 말하지 않는다
        fx_ids = tuple(i for i in fx_ids if NAME.get(i) != hooked[2])
        payload = {**payload, "triggers": [t for t in payload.get("triggers", [])
                                           if getattr(t, "name", "") != hooked[2]]}
    if not (kr and us):
        stale = (KR_IDS if not kr else set()) | (US_IDS if not us else set())
        payload = {**payload, "triggers": [t for t in payload.get("triggers", [])
                                           if getattr(t, "instrument", "") not in stale]}
    body = [
        _week(payload) if weekly else None,
        _closed_now(st),
        None,                                   # 오늘 한 줄 — 첫 장면과 겹쳐 뺀다 (9/29 짧게)
        _market(payload, ("KOSPI", "KOSDAQ"), "한국 증시", "kr", _breadth(payload)) if kr else None,
        _market(payload, ("SPX", "NASDAQ", "DOW"), "미국 증시", "us") if us else None,
        _market(payload, fx_ids, "환율 · 금리 · 원자재", "fx", priority=3),
        _flows(payload) if kr else None,
        _sectors(payload) if kr else None,
        _stocks(payload) if kr else None,
        None,                                   # 미국 대형주 — 짧게 (9/29)
        _results(payload),
        _events(payload),
        _triggers(payload),
        _macro_ref(payload, kr),
        _ahead(st, date.fromisoformat(payload["brief_date"])),
    ]
    segs = [s for s in body if s]
    if not any(s.kind in ("kr", "us", "week") for s in segs):
        return None

    d = date.fromisoformat(payload["brief_date"])
    title = "주간 브리핑" if weekly else "경제 브리핑"
    intro = Segment("", [], f"{d.month}월 {d.day}일 {WEEKDAY[d.weekday()]}요일 브리핑이에요!",
                    "intro", priority=0)
    # 브리핑 웹사이트는 알리지 않는다 — 지인·구독자 전용 (9/29 동화님)
    outro = Segment("", [], "오늘 브리핑은 여기까지예요. 다음에 또 만나요!", "outro",
                    "오늘 브리핑은 여기까지", priority=0)

    if weekly and payload.get("week"):
        m = next((m for m in payload["week"].moves if m.id == "KOSPI"), None)
        v = m.change if m else None
    else:
        # 자켓 색 — 코스피 방향. 한국장이 쉰 다음 날은 S&P 500 방향
        t = _dash(payload).get("KOSPI" if kr else "SPX")
        v = _num(t["change"]) if t else None
    mood = "down" if v is not None and v < 0 else "up"
    lead = [hooked[0]] if hooked else []
    return Script(payload["date_short"], title, mood, [*lead, intro, *segs, outro], link,
                  hooked[1] if hooked else "")
