"""리포트에 적중률이 실제로 찍히는지.

이 프로젝트가 내세우는 것은 "말한 것을 다음날 채점한다"이고, 그 증거는
페이지에 적힌 숫자뿐이다. 그런데 적중률 섹션이 다른 정리 작업에 딸려
템플릿에서 통째로 빠진 적이 있다 (2614286). 계산은 계속 돌고 변수도
넘어가고 있어서 오류 하나 없이, 페이지에서만 조용히 사라졌다.

아래 테스트는 그 숫자가 페이지에 남아 있는지만 본다.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

TPL_DIR = Path(__file__).resolve().parent.parent / "brief" / "render" / "templates"


@pytest.fixture
def env():
    e = Environment(loader=FileSystemLoader(TPL_DIR),
                    autoescape=select_autoescape(["html"]),
                    trim_blocks=True, lstrip_blocks=True)
    e.globals["eok"] = lambda *_a, **_k: ""
    return e


def base_context(**over):
    """템플릿이 요구하는 최소한의 값. 채점 부분 외에는 전부 비워 둔다."""
    ctx = dict(
        date_kr="2026년 3월 2일 (월)", is_weekly=False, tiles=[], detail={},
        case=None, case_no=0, case_total=0, kr_label="", us_label="", week=None,
        calendar=[], calendar_title="다가오는 일정", calendar_missing=[],
        basis="", trade_date="2026-03-02", verdict=None,
        score={"hit": 2, "miss": 1, "total_today": 3, "total": 3, "resolved": [
            {"claim": "코스피가 6,900 아래로 마감", "result": "hit",
             "actual": 6850.0, "op": "<", "threshold": 6900.0},
            {"claim": "국고채 3년물이 고점에서 내려온다", "result": "miss",
             "actual": 3.21, "op": "<", "threshold": 3.10},
        ]},
        track={"hit": 10, "miss": 13, "total": 23, "accuracy": 10 / 23,
               "window_days": 90, "stated_prob": 0.4,
               "actual_rate": 0.43, "calibration_n": 23},
        breakdown=[], dashboard=[], notable=[], flows=[], flow_lines=[],
        pockets=[], triggers=[], sources="", generated_at="", build_id="",
        stale="",
    )
    ctx.update(over)
    return ctx


def render(env, **over) -> str:
    return env.get_template("brief.html.j2").render(**base_context(**over))


def test_누적_적중률이_페이지에_찍힌다(env):
    html = render(env)
    assert "어제 예측 채점" in html
    assert "10/23" in html            # 누적 hit/total
    assert "43%" in html              # 적중률


def test_오늘_채점_결과가_O_X_로_나온다(env):
    html = render(env)
    assert "2/3" in html                                  # 오늘 성적
    assert "코스피가 6,900 아래로 마감" in html
    assert "국고채 3년물이 고점에서 내려온다" in html     # 빗나간 것도 숨기지 않는다


def test_아직_채점_기록이_없어도_페이지가_깨지지_않는다(env):
    html = render(env,
                  score={"hit": 0, "miss": 0, "total_today": 0, "total": 0, "resolved": []},
                  track={"hit": 0, "miss": 0, "total": 0, "accuracy": None,
                         "window_days": 90, "stated_prob": None,
                         "actual_rate": None, "calibration_n": 0})
    assert "어제 예측 채점" not in html      # 보여 줄 것이 없으면 섹션째 뺀다


# ── 지표별 성적 ─────────────────────────────────────────────
BREAKDOWN = [
    {"name": "국고채 3년물", "hit": 1, "total": 7, "pct": 14,
     "uncertain": False, "ci_lo": 3, "ci_w": 48},
    {"name": "달러인덱스", "hit": 3, "total": 3, "pct": 100,
     "uncertain": True, "ci_lo": 44, "ci_w": 56},
]


def test_지표별_성적이_펼쳐_볼_수_있게_들어간다(env):
    html = render(env, breakdown=BREAKDOWN)
    assert "지표별로 갈라 보기" in html
    assert "국고채 3년물" in html and "1/7" in html
    assert "달러인덱스" in html and "3/3" in html


def test_표본이_적으면_점추정_막대를_그리지_않는다(env):
    """'3전 3승 = 100%'를 진한 막대로 그리면 모르는 것을 아는 것처럼 보인다."""
    html = render(env, breakdown=BREAKDOWN)
    # 위쪽 채점 목록에도 같은 지표 이름이 나오므로 지표별 성적 블록만 잘라서 본다.
    block = html.split('class="bd"', 1)[1]
    rows = block.split('class="bd-row"')[1:]
    확실한행 = next(r for r in rows if "1/7" in r)
    불확실행 = next(r for r in rows if "3/3" in r)

    assert 'width:14%' in 확실한행            # 점추정을 그린다
    assert 'width:0%' in 불확실행             # 그리지 않는다
    assert 'class="bd-fill weak"' in 불확실행


def test_지표별_성적이_없으면_섹션이_나오지_않는다(env):
    assert "지표별로 갈라 보기" not in render(env, breakdown=[])
