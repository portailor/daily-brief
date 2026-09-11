"""카카오톡 요약문 생성.

카카오 텍스트 템플릿은 200자 제한이라 브리핑 전문을 담을 수 없다.
그래서 '읽고 바로 판단할 수 있는 것'만 골라 3~4개로 쪼개 보내고,
나머지는 링크로 넘긴다.

한 글자라도 넘으면 발송이 실패하므로, 여기서 미리 잘라낸다.
잘릴 때는 문장 단위로 자른다 — 말이 중간에 끊기면 안 읽힌다.
"""
from __future__ import annotations

LIMIT = 200


def fit(text: str, limit: int = LIMIT) -> str:
    """제한 안으로 줄인다. 문장 경계를 우선 살린다."""
    text = text.strip()
    if len(text) <= limit:
        return text

    cut = text[:limit]
    for sep in ("\n\n", "\n", ". ", "다. ", "습니다.", "."):
        idx = cut.rfind(sep)
        if idx > limit * 0.55:
            return cut[:idx + len(sep)].strip()
    return cut[:limit - 1].rstrip() + "…"


def _eok(v: float) -> str:
    a = abs(v)
    return f"{a / 10_000:.1f}조" if a >= 10_000 else f"{a:,.0f}억"


def build(verdict: dict, score, track: dict, notable_rows: list[dict],
          flow_stats: list, triggers: list, date_kr: str) -> list[str]:
    """카톡으로 보낼 메시지 목록. 마지막 건에 링크 버튼이 붙는다."""
    msgs: list[str] = []

    # ① 결론 + 어제 채점
    head = f"📊 {date_kr}\n\n{verdict['headline']}"
    if score.hit + score.miss > 0:
        head += f"\n\n어제 예측 {score.hit}/{score.hit + score.miss} 적중"
        if track.get("total"):
            head += f" (누적 {track['accuracy']:.0%})"
    msgs.append(fit(head))

    # ② 오늘 이례적이었던 것
    if notable_rows:
        lines = ["📈 오늘 평소와 달랐던 것"]
        for r in notable_rows[:3]:
            lines.append(f"· {r['name']} {r['change']} (평소의 {abs(r['sigma']):.1f}배)")
        msgs.append(fit("\n".join(lines)))

    # ③ 수급 — 연속 흐름이 있을 때만
    streaks = [s for s in flow_stats if abs(s.streak) >= 2]
    if streaks:
        lines = ["💰 수급"]
        for s in streaks[:3]:
            lines.append(f"· {s.market} {s.investor.replace('합계', '')} "
                         f"{_eok(s.eok)} {s.direction} ({abs(s.streak)}일 연속)")
        msgs.append(fit("\n".join(lines)))

    # ④ 내일 지켜볼 조건 + 링크
    if triggers:
        lines = ["🎯 내일 지켜볼 것"]
        for t in triggers[:2]:
            p = f" {t.probability:.0%}" if t.probability is not None else ""
            lines.append(f"· {t.claim}{p}")
        lines.append("\n↓ 전체 브리핑")
        msgs.append(fit("\n".join(lines)))

    return msgs


if __name__ == "__main__":
    sample = build(
        verdict={"headline": "국고채 3년물이 평소의 2.2배로 급등했습니다."},
        score=type("S", (), {"hit": 3, "miss": 1})(),
        track={"total": 24, "accuracy": 0.625},
        notable_rows=[
            {"name": "국고채 3년물", "change": "+8bp", "sigma": 2.21},
            {"name": "국고채 10년물", "change": "+9bp", "sigma": 1.99},
            {"name": "VIX 공포지수", "change": "-10.87%", "sigma": -1.66},
        ],
        flow_stats=[
            type("F", (), {"market": "KOSPI", "investor": "외국인합계", "eok": -21122,
                           "streak": -3, "direction": "순매도"})(),
        ],
        triggers=[
            type("T", (), {"claim": "코스피가 6,900 아래로 마감", "probability": 0.35})(),
            type("T", (), {"claim": "국고채 3년물이 4.0% 위 유지", "probability": 0.62})(),
        ],
        date_kr="2026년 9월 11일 (금)",
    )
    for i, m in enumerate(sample, 1):
        print(f"── 메시지 {i} ({len(m)}자 / {LIMIT}) " + "─" * 20)
        print(m)
        print()
