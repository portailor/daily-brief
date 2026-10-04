"""'잠깐! 경제 용어' 쇼츠 — 주 1회, 용어 하나를 25~35초에 (10/4 동화님).

  첫 장면  "잠깐! ○○에 대해 정확히 알고 계신가요?" (10/4 동화님) + 큰 글씨 용어 + 궁금증 한 줄(hook)
  뜻       한국은행 ECOS 통계용어사전 설명을 쉬운 말로 — 사전에 없는 사실은 보태지 않는다
  지금은   그 용어와 이어지는 그날 숫자(대시보드 값)가 있으면 한 줄
  끝       "다음 주에도 경제 용어 하나 알려 드릴게요!"

용어 목록과 순서는 data/terms.json. 올린 것은 data/state.json terms_done 에 남는다.
"""
from __future__ import annotations

import json
from pathlib import Path

from brief.interpret.rules import josa
from brief.shorts.script import SPOKEN, Script, Segment, _ieyo

ROOT = Path(__file__).resolve().parent.parent.parent
TERMS = ROOT / "data" / "terms.json"
SOURCE = "한국은행 경제통계시스템(ECOS) 통계용어사전"


def load(path: Path = TERMS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["terms"]


def next_term(done: list[str], terms: list[dict] | None = None) -> dict | None:
    """아직 안 올린 첫 용어. 다 썼으면 None — 되풀이하지 않는다."""
    return next((t for t in (terms or load()) if t["id"] not in done), None)


def _live(term: dict, payload: dict | None) -> tuple[str, str, str] | None:
    """(이름, 화면 값, 읽는 값) — 대시보드에 그 값이 있을 때만."""
    lv = term.get("live")
    if not lv or not payload:
        return None
    row = next((r for r in payload.get("dashboard", []) if r.get("id") == lv["id"]), None)
    val = (row or {}).get("close")
    if not val:
        return None
    unit = lv.get("unit", "")
    spoken = f"{val}{'퍼센트' if unit == '%' else unit}"
    return lv["name"], f"{val}{unit}", spoken


def build(term: dict, payload: dict | None = None, number: int = 0) -> Script:
    live = _live(term, payload)
    hook_q = term["hook"].replace("{live}", live[1] if live else "").strip()
    if "{live}" in term["hook"] and not live:                 # 숫자가 없으면 숫자 없는 질문으로
        hook_q = f"'{term['term']}', 정확히 뭘까?"
    hook = Segment("잠깐! 경제 용어", [("오늘의 경제 용어", term["term"])],
                   f"잠깐! {term['term']}에 대해 정확히 알고 계신가요? {hook_q}", "hook", hook_q, True, 0)   # '잠깐!' 손 번쩍
    mean = Segment(f"{term['term']}{josa(term['term'], '이란/란')}", [], " ".join(term["speech"]), "term",
                   term["screen"], False, 0)
    segs = [hook, mean]
    if live:
        name, shown, spoken = live
        segs.append(Segment("지금은?", [(name, shown)],
                            f"참고로 지금 {SPOKEN.get(name, name)}{josa(name, '은/는')} {spoken}{_ieyo(spoken)}.",
                            "live", "가장 최근 마감 기준", False, 1))
    segs.append(Segment("", [], "다음 주에도 경제 용어 하나 알려 드릴게요!", "outro", "잠깐 경제 용어 끝", priority=0))
    title = f"잠깐 경제 용어{f' {number}편' if number else ''}"
    return Script("", title, "up", segs, "", hook_q)


def meta(term: dict, script: Script) -> tuple[str, str]:
    """(유튜브 제목, 설명)."""
    title = f"{script.headline} | 잠깐! 경제 용어 '{term['term']}'"
    desc = "\n".join([f"잠깐! 경제 용어 — {term['term']}", "",
                      term["screen"], "", *term["speech"], "",
                      f"출처: {SOURCE} '{term['source_word']}'",
                      "공식 데이터를 모아 만든 정확한 정보의 영상입니다!",
                      f"#경제용어 #{term['term'].replace(' ', '')} #경제공부 #재테크 #Shorts"])
    return title[:100], desc
