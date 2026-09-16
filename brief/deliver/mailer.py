"""브리핑을 이메일로도 보낸다.

파일 이름이 mailer 인 이유: email.py 로 두면 표준 라이브러리 email 을 가려
직접 실행할 때 import 가 자기 자신으로 돌아온다 (html.py, calendar.py 와 같은 함정).

카카오톡 친구 발송은 카카오가 검수받지 않은 앱을 막아 두어, 받는 사람이
카카오디벨로퍼스 개발자 계정을 만들고 팀원 초대를 수락해야만 가능하다.
브리핑 하나 받자고 시킬 일이 아니라서 이메일 경로를 따로 둔다.
받는 사람은 가입할 것이 없고, 주소만 있으면 된다.

카카오톡과 달리 200자 제한이 없어 세 줄 요약에 더해 표도 함께 보낸다.
본문은 이미 만들어 둔 값만 옮겨 담는다 — 여기서 새로 쓰는 문장은 없다.

  SMTP_HOST / SMTP_PORT   기본값은 Gmail
  SMTP_USER               보내는 주소 (예: qorehdghk1215@gmail.com)
  SMTP_PASS               Gmail '앱 비밀번호' 16자리. 계정 비밀번호가 아니다.
  MAIL_TO                 받는 사람. 쉼표로 여러 명.
"""
from __future__ import annotations

import html
import re
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.collect.macro import _load_env  # noqa: E402

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 465


def recipients() -> list[str]:
    raw = _load_env().get("MAIL_TO", "")
    return [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]


def _rows(payload: dict) -> str:
    """오늘의 시장 표 — 페이지에 있는 값을 그대로 옮긴다.

    name_html 에는 말풍선 설명이 통째로 들어 있다. 메일에서는 말풍선이 동작하지
    않으므로 태그를 걷어내고 지표 이름만 남긴다. 숫자는 손대지 않는다.
    """
    out = []
    for r in payload.get("dashboard", [])[:16]:
        # name_html 은 <span class="term">코스피<span class="tip">…설명…</span></span> 꼴이다.
        # 태그만 걷어내면 설명까지 이름 뒤에 눌어붙으므로, 말풍선 앞에서 잘라낸다.
        raw = r.get("name_html", "")
        raw = raw.split('<span class="tip"', 1)[0]
        name = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        val = html.escape(str(r.get("close", "")))
        chg = str(r.get("change", "") or "")
        color = {"up": "#c0392b", "down": "#1d6fb8"}.get(r.get("dir", ""), "#555")
        asof = str(r.get("asof", "") or "")
        badge = (f'<span style="color:#999;font-size:11px;margin-left:5px">{html.escape(asof)}</span>'
                 if asof else "")
        out.append(
            f'<tr><td style="padding:7px 10px;border-bottom:1px solid #eee">'
            f'{html.escape(name)}{badge}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #eee;text-align:right">{val}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #eee;text-align:right;'
            f'color:{color};font-weight:600">{html.escape(chg)}</td></tr>')
    return "".join(out)


def build_html(message: str, link: str | None, payload: dict | None) -> str:
    lines = "".join(
        f'<div style="margin:0 0 7px">{html.escape(l)}</div>'
        for l in message.splitlines() if l.strip())
    table = _rows(payload) if payload else ""
    btn = (f'<a href="{html.escape(link)}" style="display:inline-block;background:#2563eb;'
           f'color:#fff;text-decoration:none;padding:13px 22px;border-radius:8px;'
           f'font-weight:700">전체 브리핑 보기</a>' if link else "")
    return f"""<!DOCTYPE html><html><body style="margin:0;background:#f6f7f9;padding:24px 12px;
 font-family:-apple-system,'Malgun Gothic',sans-serif;color:#1a1d21;line-height:1.7">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;padding:26px">
  <div style="font-size:15px">{lines}</div>
  {f'<table style="width:100%;border-collapse:collapse;margin:20px 0;font-size:14px">{table}</table>' if table else ''}
  <div style="margin-top:18px">{btn}</div>
  <p style="margin:22px 0 0;font-size:12px;color:#888;border-top:1px solid #eee;padding-top:14px">
    한국거래소·미 연준 FRED·한국은행 ECOS 등 공식 데이터로 자동 작성했습니다.
    투자 판단과 그 결과에 대한 책임은 읽는 사람 본인에게 있습니다.</p>
</div></body></html>"""


def send(message: str, link: str | None = None, payload: dict | None = None,
         subject: str | None = None) -> tuple[list[str], str]:
    """(보낸 주소들, 문제 설명). 설정이 없으면 조용히 건너뛴다."""
    env = _load_env()
    to = recipients()
    user, password = env.get("SMTP_USER", ""), env.get("SMTP_PASS", "")
    if not to:
        return [], ""
    if not user or not password:
        return [], "SMTP_USER / SMTP_PASS 가 설정되지 않았습니다"

    msg = EmailMessage()
    msg["Subject"] = subject or "오늘의 경제 브리핑"
    msg["From"] = formataddr(("경제 브리핑", user))
    msg["To"] = ", ".join(to)
    msg.set_content(message + (f"\n\n전체 브리핑: {link}" if link else ""))
    msg.add_alternative(build_html(message, link, payload), subtype="html")

    host = env.get("SMTP_HOST", DEFAULT_HOST)
    port = int(env.get("SMTP_PORT", DEFAULT_PORT))
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return [], ("로그인 거부 — Gmail 이라면 계정 비밀번호가 아니라 "
                    "'앱 비밀번호' 16자리를 SMTP_PASS 에 넣어야 합니다")
    except Exception as exc:                                  # noqa: BLE001
        return [], f"{type(exc).__name__}: {str(exc)[:120]}"
    return to, ""


if __name__ == "__main__":
    from datetime import datetime
    sent, problem = send(
        "✅ 경제 브리핑 연결 테스트\n\n"
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        "이 메일이 보이면 발송 경로가 정상입니다.\n"
        "내일 아침부터 매일 브리핑이 도착합니다.",
        link="https://portailor.github.io/daily-brief/",
        subject="경제 브리핑 연결 테스트")
    if sent:
        print(f"✓ 발송: {', '.join(sent)}")
    elif problem:
        print(f"✗ {problem}", file=sys.stderr)
        sys.exit(1)
    else:
        print("config/.env 의 MAIL_TO 에 받는 주소를 넣으세요.", file=sys.stderr)
        sys.exit(1)
