"""본문에서 경제 용어를 찾아 툴팁으로 감싸는 장치.

핵심 원칙: 설명 문구는 LLM이 만들지 않는다. config/terms.json 에서만 꺼낸다.
그래야 같은 용어가 매일 똑같이 설명되고, 틀릴 일이 없다.

한 번 나온 용어는 그 문서 안에서 다시 감싸지 않는다.
(같은 단어에 밑줄이 다섯 번 그어지면 읽기 방해만 된다)
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path


def term_id(canon: str) -> str:
    """용어별 고정 앵커 id. 한글을 그대로 id에 쓰지 않으려고 해시를 쓴다."""
    return "t" + hashlib.md5(canon.encode("utf-8")).hexdigest()[:8]

TERMS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "terms.json"


class Glossary:
    def __init__(self, path: Path = TERMS_PATH):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.terms = {k: v for k, v in raw.items() if not k.startswith("_")}

        # 표기 → 표제어 매핑. 긴 표기부터 매칭해야 '국고채 금리'가
        # '국고채'로 잘려나가지 않는다.
        self.lookup: dict[str, str] = {}
        for canon, body in self.terms.items():
            self.lookup[canon] = canon
            for alias in body.get("aliases", []):
                self.lookup[alias] = canon

        surfaces = sorted(self.lookup, key=len, reverse=True)
        self.pattern = re.compile("|".join(re.escape(s) for s in surfaces))

    def annotate(self, text: str, seen: set[str] | None = None,
                 mode: str = "tip") -> str:
        """텍스트를 HTML로 이스케이프하면서 용어를 표시한다.

        mode="tip"  : 그 자리에 말풍선을 띄운다. 공간이 넉넉한 본문용.
        mode="link" : 하단 용어집으로 가는 링크만 건다. 표 안처럼 좁고
                      가로 스크롤이 걸린 곳에서는 말풍선이 잘리거나
                      다음 행에 가려지므로 이쪽을 쓴다.
        """
        seen = seen if seen is not None else set()
        out: list[str] = []
        pos = 0

        for m in self.pattern.finditer(text):
            canon = self.lookup[m.group(0)]
            out.append(html.escape(text[pos:m.start()]))
            surface = html.escape(m.group(0))

            if mode == "link":
                seen.add(canon)                           # 용어집에는 실어야 한다
                out.append(f'<a class="term-link" href="#{term_id(canon)}">{surface}</a>')
            elif canon in seen:
                out.append(surface)                       # 두 번째부터는 그냥 둔다
            else:
                seen.add(canon)
                out.append(self._markup(m.group(0), canon))
            pos = m.end()

        out.append(html.escape(text[pos:]))
        return "".join(out)

    def _markup(self, surface: str, canon: str) -> str:
        t = self.terms[canon]
        short = html.escape(t["short"])
        full = html.escape(t["full"])
        pocket = html.escape(t.get("pocket", ""))
        pocket_html = f'<em class="tip-pocket">{pocket}</em>' if pocket else ""

        return (
            f'<span class="term" tabindex="0">{html.escape(surface)}'
            f'<span class="tip">'
            f'<strong class="tip-head">{html.escape(canon)} · {short}</strong>'
            f'<span class="tip-body">{full}</span>'
            f'{pocket_html}'
            f'</span></span>'
        )

    def used_terms(self, seen: set[str]) -> list[dict]:
        """문서 하단 '오늘 나온 용어' 목록용. 난이도 순으로 정렬."""
        items = [{"name": k, "id": term_id(k), **self.terms[k]}
                 for k in seen if k in self.terms]
        return sorted(items, key=lambda x: (x["level"], x["name"]))


if __name__ == "__main__":
    g = Glossary()
    seen: set[str] = set()
    sample = ("WTI 원유가 하락했지만 미 국채 10년물 금리는 올랐습니다. "
              "VIX는 안정적이고, 외국인 순매수는 이틀째 이어졌습니다. "
              "WTI는 다시 언급해도 밑줄이 한 번만 그어집니다.")
    print(g.annotate(sample, seen)[:600])
    print("\n감지된 용어:", ", ".join(sorted(seen)))
    print(f"사전 총 {len(g.terms)}개 표제어 / 표기 {len(g.lookup)}개")
