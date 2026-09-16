"""브리핑을 이메일로도 보낸다.

파일 이름이 mailer 인 이유: email.py 로 두면 표준 라이브러리 email 을 가려
직접 실행할 때 import 가 자기 자신으로 돌아온다 (html.py, calendar.py 와 같은 함정).

카카오톡 친구 발송은 카카오가 검수받지 않은 앱을 막아 두어, 받는 사람이
카카오디벨로퍼스 개발자 계정을 만들고 팀원 초대를 수락해야만 가능하다.
브리핑 하나 받자고 시킬 일이 아니라서 이메일 경로를 따로 둔다.
받는 사람은 가입할 것이 없고, 주소만 있으면 된다.

메일은 짧게 간다. 세 줄 요약 + 막대그래프 두 개 + 외국인 매매 두 줄 + 링크.
전체 표는 넣지 않는다 — 링크를 누르면 어차피 다 보인다(동화님 피드백).
여기서 새로 쓰는 문장은 없다 — 전부 이미 계산된 값을 옮겨 담을 뿐이다.

  SMTP_HOST / SMTP_PORT   기본값은 Gmail
  SMTP_USER               보내는 주소
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

UP, DOWN, FLAT = "#d64545", "#2f6fd6", "#9ca3af"
TRACK = "#f1f3f5"

# 막대그래프에 올릴 지표. 금리(bp)는 %와 눈금이 달라 섞지 않는다.
BAR_IDS = ("KOSPI", "KOSDAQ", "SPX", "NASDAQ", "USDKRW")

# ── 메일 호환 원칙 ───────────────────────────────────────────
# 받는 사람이 Gmail·네이버·다음·Outlook 어느 것을 쓸지 모른다. 이 중 가장 좁은
# 쪽에 맞춘다: 레이아웃은 전부 <table>, 스타일은 전부 인라인, 색은 bgcolor
# 속성을 함께 준다. SVG·스크립트·flex·class·외부 CSS 는 쓰지 않는다
# (Gmail 은 SVG 를 지우고, 네이버·Outlook 은 flex 를 무시한다).


def recipients() -> list[str]:
    raw = _load_env().get("MAIL_TO", "")
    return [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]


def _name(row: dict) -> str:
    """name_html 은 <span class="term">코스피<span class="tip">…</span></span> 꼴.
    태그만 걷어내면 말풍선 설명까지 이름 뒤에 눌어붙으므로 말풍선 앞에서 자른다."""
    raw = row.get("name_html", "").split('<span class="tip"', 1)[0]
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def _num(text: str) -> float | None:
    """'-3.26%' → -3.26. 퍼센트가 아니면 None."""
    t = str(text or "").strip().replace("−", "-")
    if not t.endswith("%"):
        return None
    try:
        return float(t[:-1].replace(",", "").replace("+", ""))
    except ValueError:
        return None


def _eok(v: float) -> str:
    a = abs(v)
    return f"{a / 10_000:.1f}조" if a >= 10_000 else f"{a:,.0f}억"


def _title(text: str, first: bool = False) -> str:
    top = 14 if first else 22
    return (f'<tr><td style="padding:{top}px 0 8px 0;font-size:12px;font-weight:bold;'
            f'color:#6b7280">{html.escape(text)}</td></tr>')


def _bars(items: list[tuple[str, float]]) -> str:
    """가운데를 0으로 두고 오른쪽(빨강)·왼쪽(파랑)으로 뻗는 막대.

    막대 길이는 이 묶음에서 가장 크게 움직인 값을 100% 로 잡은 상대 길이다.
    숫자는 옆에 그대로 적으므로 길이만 보고 오해할 일은 없다.
    """
    if not items:
        return ""
    peak = max(abs(v) for _, v in items) or 1.0
    rows = []
    for label, v in items:
        w = max(2, round(abs(v) / peak * 100))            # 0 에 가까워도 점은 보이게
        color = UP if v > 0 else DOWN if v < 0 else FLAT
        bar = (f'<table width="{w}%" cellpadding="0" cellspacing="0" border="0" '
               f'align="{"left" if v >= 0 else "right"}"><tr>'
               f'<td height="10" bgcolor="{color}" style="background:{color};'
               f'height:10px;line-height:10px;font-size:1px">&nbsp;</td></tr></table>')
        left = bar if v < 0 else "&nbsp;"
        right = bar if v >= 0 else "&nbsp;"
        rows.append(
            f'<tr>'
            f'<td width="92" style="width:92px;padding:5px 6px 5px 0;font-size:13px;'
            f'color:#1f2328;white-space:nowrap">{html.escape(label)}</td>'
            f'<td width="40%" bgcolor="{TRACK}" style="background:{TRACK};padding:0">{left}</td>'
            f'<td width="1" bgcolor="#c9ced4" style="background:#c9ced4;padding:0;'
            f'font-size:1px">&nbsp;</td>'
            f'<td width="40%" bgcolor="{TRACK}" style="background:{TRACK};padding:0">{right}</td>'
            f'<td width="60" align="right" style="padding:5px 0 5px 8px;font-size:13px;'
            f'font-weight:bold;color:{color};white-space:nowrap">{v:+.2f}%</td>'
            f'</tr>')
    return (f'<tr><td><table width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'{"".join(rows)}</table></td></tr>')


def _foreign(kr: dict) -> str:
    f = kr.get("foreign") or {}
    if not f.get("buy") or not f.get("sell"):
        return ""

    def line(tag: str, color: str, xs: list) -> str:
        names = " · ".join(f'{html.escape(x["name"])} {_eok(x["net_eok"])}' for x in xs[:2])
        return (f'<tr><td style="padding:3px 0;font-size:13.5px;color:#1f2328">'
                f'<b style="color:{color}">{tag}</b>&nbsp; {names}</td></tr>')

    return (_title("외국인이 가장 많이 산 · 판 종목")
            + line("산 종목", UP, f["buy"]) + line("판 종목", DOWN, f["sell"]))


def build_html(message: str, link: str | None, payload: dict | None) -> str:
    payload = payload or {}
    dash = payload.get("dashboard") or []
    kr = (payload.get("detail") or {}).get("kr") or {}

    lines = [x.strip() for x in message.splitlines() if x.strip()]
    title = lines[0] if lines else "경제 브리핑"
    # 카톡의 세 줄 요약은 싣지 않는다. 아래 그래프(지수·업종)와 외국인 줄이
    # 같은 내용을 더 한눈에 보여주므로, 넣으면 같은 숫자를 두 번 읽게 된다.

    # 지수·환율 등락 막대
    idx = []
    for i in BAR_IDS:
        for r in dash:
            v = _num(r.get("change")) if r.get("id") == i else None
            if v is not None:
                idx.append((_name(r), v))

    # 업종 — 가장 오른 셋, 가장 내린 셋
    sec = kr.get("sectors") or []
    sec_items = ([(s["name"], s["chg_pct"]) for s in sec[:3]]
                 + [(s["name"], s["chg_pct"]) for s in sec[-3:]]) if len(sec) >= 6 else []

    button = ""
    if link:
        button = (
            f'<tr><td style="padding:24px 0 0 0">'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td align="center" bgcolor="#2563eb" style="background:#2563eb;padding:13px 0">'
            f'<a href="{html.escape(link)}" target="_blank" style="color:#ffffff;'
            f'font-size:15px;font-weight:bold;text-decoration:none">전체 브리핑 보기 →</a>'
            f'</td></tr></table>'
            f'<div style="padding-top:6px;font-size:11.5px;color:#9ca3af;text-align:center">'
            f'전체 지표 · 시가총액 상위 · 공시와 뉴스 · 오늘의 시사상식</div></td></tr>')

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#f4f5f7">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f5f7">'
        '<tr><td align="center" style="padding:18px 10px">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" '
        'style="max-width:520px;background:#ffffff;font-family:\'Malgun Gothic\','
        '\'Apple SD Gothic Neo\',sans-serif;line-height:1.6">'
        '<tr><td style="padding:22px 20px 20px 20px">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="padding-bottom:10px;border-bottom:2px solid #1f2328;font-size:17px;'
        f'font-weight:bold;color:#1f2328">{html.escape(title)}</td></tr>'
        + (_title("지수 · 환율 등락", first=True) + _bars(idx) if idx else "")
        + (_title("업종 — 가장 오른 셋 · 가장 내린 셋") + _bars(sec_items) if sec_items else "")
        + _foreign(kr)
        + button
        + '<tr><td style="padding-top:18px;font-size:11px;color:#9ca3af;line-height:1.55">'
          '한국거래소 · 미 연준 FRED · 한국은행 ECOS 공식 데이터로 자동 작성했습니다. '
          '전망이나 추천은 없으며, 투자 판단의 책임은 본인에게 있습니다.</td></tr>'
        '</table></td></tr></table>'
        '</td></tr></table></body></html>')


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
    import json

    box_path = ROOT / "data" / "outbox.json"
    if len(sys.argv) > 1 and sys.argv[1] == "preview" and box_path.exists():
        box = json.loads(box_path.read_text(encoding="utf-8"))
        out = ROOT / "data" / "mail_preview.html"
        out.write_text(build_html(box["message"], box.get("link"), box), encoding="utf-8")
        print(f"미리보기 저장: {out}")
        sys.exit(0)

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
