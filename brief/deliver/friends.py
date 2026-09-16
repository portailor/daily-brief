"""친구에게도 브리핑 보내기 — 친구 목록 조회 + 친구 메시지 발송.

카카오는 아무에게나 메시지를 못 보내게 막아 두었다. 받는 사람이 친구 목록에
나타나려면 아래를 모두 만족해야 한다 (카카오 문서 '친구 정보 제공 조건').
  1) 보내는 사람(동화님) 토큰에 'friends' 동의항목
  2) 받는 사람이 이 앱에 연결(카카오 로그인)되어 있을 것
  3) 받는 사람도 연결할 때 '카카오 서비스 내 친구목록'에 동의했을 것
     — 처음 만든 초대 페이지는 talk_message 만 요청해서 여기서 막혔다
  4) 서로 카카오톡 친구이고, 숨김·차단이 아니며, 받는 사람 프로필이 공개일 것
  5) 검수 전 앱은 받는 사람이 앱 '팀원'(카카오디벨로퍼스 멤버)일 것

2번을 위해 config/friends.json 에 받을 사람의 uuid 를 적어 둔다.
uuid 는 `python brief/deliver/friends.py list` 로 확인한다.

발송 한도(카카오 문서): 검수 전에는 하루 30건. 한 번에 최대 5명.
연락이 닿지 않아도 동화님 본인 발송은 막지 않는다 — 친구 발송 실패는 경고로만 남긴다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
FRIENDS_PATH = ROOT / "config" / "friends.json"

sys.path.insert(0, str(ROOT))
from brief.deliver.kakao import KakaoError, refresh_access_token  # noqa: E402
from brief.retry import with_retry                               # noqa: E402

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
INVITE_REDIRECT = "https://portailor.github.io/daily-brief/invite.html"
LIST_URL = "https://kapi.kakao.com/v1/api/talk/friends"
SEND_URL = "https://kapi.kakao.com/v1/api/talk/friends/message/default/send"
MAX_RECEIVERS = 5           # 카카오 제한: 한 요청에 최대 5명
NL = chr(10)


def fetch_friends(access_token: str) -> list[dict]:
    """이 앱에 연결된 카카오톡 친구 목록. 연결하지 않은 친구는 나오지 않는다."""
    res = with_retry(lambda: requests.get(
        LIST_URL, headers={"Authorization": f"Bearer {access_token}"},
        params={"limit": 100}, timeout=20), label="카카오 친구 목록")

    if res.status_code != 200:
        body = res.text
        if "team member" in body:
            why = ("  검수받지 않은 앱은 '팀원'으로 등록된 사람에게만 쓸 수 있습니다." + NL +
                   "  콘솔 > 앱 설정 > 멤버 에서 받을 사람을 팀원으로 초대하고," + NL +
                   "  그 사람이 초대를 수락한 뒤 다시 실행하세요.")
        elif "scope" in body:
            why = ("  '카카오 서비스 내 친구목록' 동의항목이 꺼져 있습니다." + NL +
                   "  콘솔 > 카카오 로그인 > 동의항목에서 '이용 중 동의'로 켠 뒤," + NL +
                   "  python brief/deliver/kakao_auth.py 로 재인증하세요.")
        else:
            why = "  동의항목과 재인증(kakao_auth.py) 상태를 확인하세요."
        raise KakaoError(f"친구 목록 조회 실패 [{res.status_code}] {body}" + NL + why)
    return res.json().get("elements", [])


def connect(code: str) -> str:
    """친구가 초대 페이지에서 받아온 인가 코드를 토큰으로 바꿔 '연결된 사용자'로 만든다.

    토큰 자체는 쓰지 않고 버린다. 우리에게 필요한 건 연결됐다는 사실뿐이고,
    실제 발송은 동화님 토큰으로 한다. 남의 토큰을 보관할 이유가 없다.
    """
    cfg = json.loads((ROOT / "config" / ".kakao_token.json").read_text(encoding="utf-8"))
    payload = {"grant_type": "authorization_code",
               "client_id": cfg["rest_api_key"],
               "redirect_uri": INVITE_REDIRECT,
               "code": code}
    if cfg.get("client_secret"):
        payload["client_secret"] = cfg["client_secret"]

    res = requests.post(TOKEN_URL, data=payload, timeout=20)
    if res.status_code != 200:
        raise KakaoError(
            f"연결 실패 [{res.status_code}] {res.text}" + chr(10) +
            "코드는 10분이면 만료되고 한 번만 쓸 수 있습니다. "
            "초대 페이지를 다시 열어 새 코드를 받으세요.")

    tok = res.json()
    auth = {"Authorization": f"Bearer {tok['access_token']}"}
    me = requests.get("https://kapi.kakao.com/v2/user/me", headers=auth, timeout=20)
    nick = ""
    if me.status_code == 200:
        nick = (me.json().get("properties") or {}).get("nickname", "")

    # 친구 목록에 안 나타날 때 원인을 바로 보기 위해, 실제로 동의한 항목을 확인한다
    sc = requests.get("https://kapi.kakao.com/v2/user/scopes", headers=auth, timeout=20)
    if sc.status_code == 200:
        agreed = {x["id"]: x.get("agreed") for x in sc.json().get("scopes", [])}
        print(f"  토큰 scope: {tok.get('scope', '')}")
        for key in ("friends", "talk_message"):
            print(f"  {key:<13} 동의: {'예' if agreed.get(key) else '아니오'}")
    return nick


def load_recipients() -> list[dict]:
    """config/friends.json — [{"uuid": "...", "name": "홍길동"}, ...]"""
    if not FRIENDS_PATH.exists():
        return []
    try:
        data = json.loads(FRIENDS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [f for f in data.get("recipients", []) if f.get("uuid")]


def send_to_friends(text: str, link_url: str | None = None,
                    button_title: str = "전체 브리핑 보기",
                    access_token: str | None = None) -> tuple[list[str], list[str]]:
    """등록된 친구들에게 같은 메시지를 보낸다. (보낸 이름들, 문제 설명들)"""
    people = load_recipients()
    if not people:
        return [], []

    token = access_token or refresh_access_token()
    template: dict = {"object_type": "text", "text": text}
    if link_url:
        template["link"] = {"web_url": link_url, "mobile_web_url": link_url}
        template["button_title"] = button_title
    else:
        template["link"] = {}

    by_uuid = {p["uuid"]: p.get("name") or p["uuid"][:8] for p in people}
    sent: list[str] = []
    problems: list[str] = []

    for i in range(0, len(people), MAX_RECEIVERS):
        chunk = [p["uuid"] for p in people[i:i + MAX_RECEIVERS]]
        try:
            res = with_retry(lambda: requests.post(
                SEND_URL,
                headers={"Authorization": f"Bearer {token}"},
                data={"receiver_uuids": json.dumps(chunk),
                      "template_object": json.dumps(template, ensure_ascii=False)},
                timeout=20), label="카카오 친구 발송")
        except Exception as exc:                                  # noqa: BLE001
            problems.append(f"{', '.join(by_uuid[u] for u in chunk)}: {type(exc).__name__}")
            continue

        if res.status_code != 200:
            problems.append(f"{', '.join(by_uuid[u] for u in chunk)}: "
                            f"[{res.status_code}] {res.text[:120]}")
            continue

        body = res.json()
        sent += [by_uuid.get(u, u) for u in body.get("successful_receiver_uuids", [])]
        for f in body.get("failure_info", []) or []:
            who = ", ".join(by_uuid.get(u, u) for u in f.get("receiver_uuids", []))
            problems.append(f"{who}: {f.get('msg', '알 수 없는 오류')}")

    return sent, problems


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        tok = refresh_access_token()
        try:
            friends = fetch_friends(tok)
        except KakaoError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

        if not friends:
            print("이 앱에 연결된 카카오톡 친구가 아직 없습니다.")
            print("친구가 초대 링크로 한 번 로그인해야 여기에 나타납니다.")
            sys.exit(0)

        print(f"연결된 친구 {len(friends)}명 — config/friends.json 에 넣을 값:\n")
        print(json.dumps({"recipients": [
            {"name": f.get("profile_nickname") or "이름없음", "uuid": f["uuid"]}
            for f in friends]}, ensure_ascii=False, indent=2))

    elif cmd == "test":
        people = load_recipients()
        if not people:
            print("config/friends.json 에 받을 사람이 없습니다.", file=sys.stderr)
            sys.exit(1)
        sent, problems = send_to_friends(
            "✅ 경제 브리핑 연결 테스트\n\n"
            "이 메시지가 보이면 발송 경로가 정상입니다.\n"
            "내일 아침부터 매일 브리핑이 도착합니다.",
            link_url="https://portailor.github.io/daily-brief/")
        print(f"✓ 발송: {', '.join(sent) if sent else '없음'}")
        for p in problems:
            print(f"✗ {p}", file=sys.stderr)

    elif cmd == "connect":
        if len(sys.argv) < 3:
            print("사용법: python brief/deliver/friends.py connect <코드>", file=sys.stderr)
            sys.exit(2)
        try:
            nick = connect(sys.argv[2])
        except KakaoError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ 연결됐습니다{f' — {nick}' if nick else ''}.")
        print("  이제 `python brief/deliver/friends.py list` 로 uuid 를 확인하세요.")

    else:
        print("사용법: python brief/deliver/friends.py [list|connect <코드>|test]",
              file=sys.stderr)
        sys.exit(2)
