"""API 키를 화면에 보이지 않게 입력받아 config/.env 와 GitHub Secrets 에 넣는다.

  python scripts/set_keys.py SOSOVALUE_API_KEY COINMARKETCAL_API_KEY

붙여 넣어도 글자가 안 보이는 게 정상이다(비밀번호 입력처럼). 값은 어디에도 찍지 않는다.
공개 저장소라서 키를 채팅이나 로그에 남기지 않기 위한 도구.
"""
from __future__ import annotations

import getpass
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "config" / ".env"


def _set_env(key: str, value: str) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [l for l in lines if not l.strip().startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(names: list[str]) -> int:
    if not names:
        print(__doc__)
        return 1
    for name in names:
        value = getpass.getpass(f"{name} 붙여넣기 (안 보여도 정상) 후 Enter: ").strip()
        if not value:
            print(f"  {name} 건너뜀 (빈 값)")
            continue
        _set_env(name, value)
        p = subprocess.run(["gh", "secret", "set", name], input=value, text=True,
                           capture_output=True, cwd=ROOT)
        ok = p.returncode == 0
        print(f"  ✓ {name}: config/.env 저장 · GitHub Secret {'등록' if ok else '등록 실패'}"
              + ("" if ok else f" ({p.stderr.strip()[:150]})") + f" · {len(value)}자")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
