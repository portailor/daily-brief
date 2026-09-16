"""브리핑을 이메일로도 보낸다.

파일 이름이 mailer 인 이유: email.py 로 두면 표준 라이브러리 email 을 가려
직접 실행할 때 import 가 자기 자신으로 돌아온다 (html.py, calendar.py 와 같은 함정).

카카오톡 친구 발송은 카카오가 검수받지 않은 앱을 막아 두어, 받는 사람이
카카오디벨로퍼스 개발자 계정을 만들고 팀원 초대를 수락해야만 가능하다.
브리핑 하나 받자고 시킬 일이 아니라서 이메일 경로를 따로 둔다.
받는 사람은 가입할 것이 없고, 주소만 있으면 된다.

카카오톡의 200자 제한이 없으니 요약을 조금 더 담는다. 다만 길다고 좋은 게
아니라서, 표로 끊어 한눈에 들어오게 하고 중요한 것만 남긴다.
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

UP, DOWN, FLAT = "#c0392b", "#1d6fb8", "#6b7280"
CARD_IDS = ("KOSPI", "KOSDAQ", "SPX", "NASDAQ", "USDKRW", "US10Y")


def recipients() -> list[str]:
    raw = _load_env().get("MAIL_TO", "")
    return [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]


# ── 본문 조각들 ──────────────────────────────────────────────

def _color(direction: str) -> str:
    return {"up": UP, "down": DOWN}.get(direction, FLAT)


def _name(row: dict) -> str:
    """지표 이름만 꺼낸다.

    name_html 은 <span class="term">코스피<span class="tip">…설명…</span></span>
    꼴이라, 태그만 걷어내면 말풍선 설명까지 이름 뒤에 눌어붙는다.
    """
    raw = row.get("name_html", "").split('<span class="tip"', 1)[0]
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def _eok(v: float) -> str:
    a = abs(v)
    return f"{a / 10_000:.1f}조" if a >= 10_000 else f"{a:,.0f}억"


def _h2(text: str) -> str:
    return (f'<div style="font-size:11.5px;font-weight:700;color:#6b7280;'
            f'letter-spacing:.4px;margin:22px 0 7px">{html.escape(text)}</div>')


def _cards(dash: list) -> str:
    """대표 지표 여섯 개 — 표를 읽기 전에 눈에 먼저 들어오는 층."""
    pick = [r for i in CARD_IDS for r in dash if r.get("id") == i]
    if not pick:
        return ""
    cells = [
        f'<td style="width:33%;padding:10px 6px;text-align:center;'
        f'background:#f8f9fb;border-radius:9px">'
        f'<div style="font-size:11px;color:#6b7280">{html.escape(_name(r))}</div>'
        f'<div style="font-size:16.5px;font-weight:700;margin:2px 0 1px">'
        f'{html.escape(str(r.get("close", "")))}</div>'
        f'<div style="font-size:12.5px;font-weight:700;color:{_color(r.get("dir", ""))}">'
        f'{html.escape(str(r.get("change", "")))}</div></td>'
        for r in pick]
    rows = "".join(f'<tr>{"".join(cells[i:i + 3])}</tr>'
                   for i in range(0, len(cells), 3))
    return (f'<table style="width:100%;border-collapse:separate;'
            f'border-spacing:5px;margin:14px 0 0">{rows}</table>')


def _two_col(left_title: str, left: list, right_title: str, right: list) -> str:
    def col(title: str, items: list) -> str:
        lis = "".join(f'<div style="font-size:13.5px;padding:2.5px 0">{x}</div>'
                      for x in items)
        return (f'<td style="width:50%;vertical-align:top;padding:0 5px">'
                f'<div style="font-size:11px;font-weight:700;color:#9ca3af;'
                f'margin-bottom:3px">{html.escape(title)}</div>{lis}</td>')
    return (f'<table style="width:100%;border-collapse:collapse">'
            f'<tr>{col(left_title, left)}{col(right_title, right)}</tr></table>')


def _pct(name: str, pct: float) -> str:
    return (f'{html.escape(name)} <b style="color:{UP if pct > 0 else DOWN}">'
            f'{pct:+.1f}%</b>')


def _sectors(kr: dict) -> str:
    sec = kr.get("sectors") or []
    if len(sec) < 4:
        return ""
    return (_h2("업종 — 어디가 오르고 어디가 내렸나")
            + _two_col("오른 업종", [_pct(x["name"], x["chg_pct"]) for x in sec[:3]],
                       "내린 업종", [_pct(x["name"], x["chg_pct"]) for x in sec[-3:][::-1]]))


def _foreign(kr: dict) -> str:
    f = kr.get("foreign") or {}
    if not f.get("buy") or not f.get("sell"):
        return ""

    def fmt(x: dict) -> str:
        chg = x.get("chg_pct", 0)
        return (f'{html.escape(x["name"])} <b>{_eok(x["net_eok"])}</b>'
                f'<span style="color:{UP if chg > 0 else DOWN};font-size:12px">'
                f' {chg:+.1f}%</span>')

    return (_h2("외국인이 산 종목 · 판 종목")
            + _two_col("많이 산 종목", [fmt(x) for x in f["buy"][:3]],
                       "많이 판 종목", [fmt(x) for x in f["sell"][:3]]))


def _movers(kr: dict) -> str:
    gain, lose = kr.get("gainers") or [], kr.get("losers") or []
    if not gain and not lose:
        return ""
    return (_h2("크게 움직인 종목")
            + _two_col("많이 오른 종목", [_pct(x["name"], x["chg_pct"]) for x in gain[:3]],
                       "많이 내린 종목", [_pct(x["name"], x["chg_pct"]) for x in lose[:3]]))


def _breadth(kr: dict) -> str:
    b = kr.get("breadth") or {}
    parts = [f'<span style="margin-right:16px">{m} '
             f'<b style="color:{UP}">오름 {b[m]["up"]}</b> · '
             f'<b style="color:{DOWN}">내림 {b[m]["down"]}</b></span>'
             for m in ("KOSPI", "KOSDAQ") if b.get(m)]
    if not parts:
        return ""
    return (_h2("오른 종목 수 / 내린 종목 수")
            + f'<div style="font-size:13.5px">{"".join(parts)}</div>')


def _rows(dash: list) -> str:
    out = []
    for r in dash:
        asof = str(r.get("asof", "") or "")
        badge = (f'<span style="color:#b6bbc2;font-size:11px;margin-left:5px">'
                 f'{html.escape(asof)}</span>' if asof else "")
        out.append(
            f'<tr><td style="padding:6px 8px;border-bottom:1px solid #f1f2f4;'
            f'font-size:13.5px">{html.escape(_name(r))}{badge}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f1f2f4;'
            f'text-align:right;font-size:13.5px">{html.escape(str(r.get("close", "")))}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f1f2f4;text-align:right;'
            f'font-size:13.5px;font-weight:600;color:{_color(r.get("dir", ""))}">'
            f'{html.escape(str(r.get("change", "")))}</td></tr>')
    if not out:
        return ""
    return f'<table style="width:100%;border-collapse:collapse">{"".join(out)}</table>'


def build_html(message: str, link: str | None, payload: dict | None) -> str:
    payload = payload or {}
    dash = payload.get("dashboard") or []
    kr = (payload.get("detail") or {}).get("kr") or {}

    lines = [line.strip() for line in message.splitlines() if line.strip()]
    title = lines[0] if lines else "경제 브리핑"
    summary = "".join(
        f'<div style="font-size:14.5px;padding:3px 0">{html.escape(line)}</div>'
        for line in lines[1:] if not line.startswith("종목·업종 상세"))

    btn = (f'<div style="margin:26px 0 0"><a href="{html.escape(link)}" '
           f'style="display:block;text-align:center;background:#2563eb;color:#fff;'
           f'text-decoration:none;padding:14px;border-radius:9px;font-weight:700;'
           f'font-size:15px">전체 브리핑 보기</a></div>'
           f'<div style="font-size:11.5px;color:#9ca3af;text-align:center;margin-top:7px">'
           f'시가총액 상위 · 업종 전체 · 공시와 뉴스 · 오늘의 시사상식</div>'
           if link else "")

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;background:#f4f5f7;padding:20px 10px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Malgun Gothic',sans-serif;"
        'color:#1a1d21;line-height:1.65">'
        '<div style="max-width:580px;margin:0 auto;background:#fff;'
        'border-radius:14px;padding:24px 20px">'
        f'<div style="font-size:17px;font-weight:800;padding-bottom:11px;'
        f'border-bottom:2px solid #1a1d21">{html.escape(title)}</div>'
        f'<div style="margin-top:11px">{summary}</div>'
        f'{_cards(dash)}{_sectors(kr)}{_foreign(kr)}{_movers(kr)}{_breadth(kr)}'
        f'{_h2("오늘의 시장 전체")}{_rows(dash)}{btn}'
        '<div style="margin-top:20px;padding-top:12px;border-top:1px solid #eef0f2;'
        'font-size:11.5px;color:#9ca3af;line-height:1.6">'
        '한국거래소 · 미 연준 FRED · 한국은행 ECOS · 금융감독원 DART 의 공식 '
        '데이터로 자동 작성했습니다. 사람이 쓴 전망이나 추천은 들어 있지 않습니다.<br>'
        '투자 판단과 그 결과에 대한 책임은 읽는 사람 본인에게 있습니다.'
        '</div></div></body></html>')


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
