"""유튜브 주간 성과 보고 — 매주 일요일 저녁 동화님 메일로 (10/9 동화님: "감으로 판단하지 말고 숫자로").

  python scripts/yt_report.py            # 화면에만 출력
  python scripts/yt_report.py --mail     # 동화님 메일(SMTP_USER, 보내는 주소 = 받는 주소)로도

  지난 7일 동안 올라간 영상마다: 조회수 · 좋아요 · 댓글, 그리고 (유튜브 분석 API 를 쓸 수 있으면)
  평균 시청 비율 · 평균 시청 시간 · 그 영상으로 늘어난 구독자. 종류(오늘의 숫자 · 경제 용어 · 에피소드)별
  평균, 시청 경로(쇼츠 피드·검색·채널 등), 지난 7일 새 댓글.
  숫자만 옮긴다 — 해석이나 원인은 쓰지 않는다. 친구 목록(MAIL_TO)에는 보내지 않는다.
  공개 저장소라 결과를 파일로 남기지 않는다. 토큰은 출력하지 않는다.
"""
from __future__ import annotations

import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from brief.collect.macro import _load_env       # noqa: E402
from brief.deliver import youtube               # noqa: E402

API = "https://www.googleapis.com/youtube/v3/"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
KST = timezone(timedelta(hours=9))
KIND = {"brief": "오늘의 숫자", "terms": "잠깐! 경제 용어", "episodes": "하차니 에피소드"}
SOURCE = {"SHORTS": "쇼츠 피드", "YT_SEARCH": "유튜브 검색", "YT_CHANNEL": "채널 페이지",
          "PLAYLIST": "재생목록", "SUBSCRIBER": "구독 피드", "BROWSE": "홈·탐색", "RELATED_VIDEO": "추천 영상",
          "EXT_URL": "외부 링크", "NO_LINK_OTHER": "직접·기타", "YT_OTHER_PAGE": "유튜브 기타",
          "NOTIFICATION": "알림", "HASHTAGS": "해시태그", "SOUND_PAGE": "사운드"}


def kind(title: str) -> str:
    if "잠깐! 경제 용어" in title:
        return "terms"
    if any(k in title for k in ("오늘의 숫자", "지난주의 숫자", "경제 브리핑", "주간 브리핑")):
        return "brief"
    return "episodes"


def _get(url: str, token: str, **params) -> dict:
    r = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    body = r.json()
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code} {body.get('error', {}).get('message', '')[:160]}")
    return body


def collect(today: date) -> dict:
    token = youtube._access_token(youtube._env())
    ch = _get(API + "channels", token, part="id,statistics,contentDetails", mine="true")["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, page = [], None
    while True:
        r = _get(API + "playlistItems", token, part="contentDetails", playlistId=uploads, maxResults=50,
                 **({"pageToken": page} if page else {}))
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        page = r.get("nextPageToken")
        if not page:
            break
    videos = []
    for k in range(0, len(ids), 50):
        for v in _get(API + "videos", token, part="snippet,statistics,status", id=",".join(ids[k:k + 50]))["items"]:
            if v["status"]["privacyStatus"] != "public":
                continue
            st = v["statistics"]
            videos.append({"id": v["id"], "title": v["snippet"]["title"], "kind": kind(v["snippet"]["title"]),
                           "published": datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
                           .astimezone(KST).date(),
                           "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                           "comments": int(st.get("commentCount", 0))})

    start, end = today - timedelta(days=7), today - timedelta(days=1)
    out = {"channel": ch["statistics"], "videos": videos, "start": start, "end": end,
           "analytics_error": "", "sources": [], "week": {}, "comments": []}
    # 유튜브 분석 — Google Cloud 에서 'YouTube Analytics API' 를 켜야 쓸 수 있다
    try:
        a = _get(ANALYTICS, token, ids="channel==MINE", startDate="2026-09-01", endDate=end.isoformat(),
                 metrics="views,averageViewDuration,averageViewPercentage,subscribersGained",
                 dimensions="video", sort="-views", maxResults=200)
        per = {r[0]: r[1:] for r in a.get("rows") or []}
        for v in videos:
            if v["id"] in per:
                _views, dur, pct, subs = per[v["id"]]
                v.update(avg_sec=dur, avg_pct=pct, subs=subs)
        w = _get(ANALYTICS, token, ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
                 metrics="views,subscribersGained,subscribersLost")
        if w.get("rows"):
            out["week"] = dict(zip(("views", "gained", "lost"), w["rows"][0]))
        s = _get(ANALYTICS, token, ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
                 metrics="views", dimensions="insightTrafficSourceType", sort="-views")
        out["sources"] = [(SOURCE.get(r[0], r[0]), r[1]) for r in s.get("rows") or []]
    except Exception as exc:                                  # noqa: BLE001
        out["analytics_error"] = str(exc)
    # 지난 7일 새 댓글 (용어 신청이 오면 다음 대본에 쓸 수 있게)
    try:
        r = _get(API + "commentThreads", token, part="snippet", allThreadsRelatedToChannelId=ch["id"], maxResults=50)
        titles = {v["id"]: v["title"] for v in videos}
        for t in r.get("items", []):
            c = t["snippet"]["topLevelComment"]["snippet"]
            when = datetime.fromisoformat(c["publishedAt"].replace("Z", "+00:00")).astimezone(KST).date()
            if when >= start:
                out["comments"].append((when, titles.get(t["snippet"]["videoId"], t["snippet"]["videoId"]),
                                        c["textOriginal"].replace("\n", " ")[:120], t["snippet"]["totalReplyCount"]))
    except Exception as exc:                                  # noqa: BLE001
        out["comments_error"] = str(exc)
    return out


def _avg(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _line(v: dict) -> str:
    extra = ""
    if "avg_pct" in v:
        extra = f" · 평균 {v['avg_pct']:.0f}% 시청({v['avg_sec']:.0f}초) · 구독 +{v['subs']}"
    return (f"  {v['published'].month}/{v['published'].day} {v['views']:>6,}회 · 좋아요 {v['likes']} · "
            f"댓글 {v['comments']}{extra}\n      {v['title'][:60]}")


def text(d: dict) -> str:
    ch, start, end = d["channel"], d["start"], d["end"]
    week = [v for v in d["videos"] if start <= v["published"] <= end]
    out = [f"하차니의 데일리 브리핑 · 주간 성과 ({start.month}/{start.day} ~ {end.month}/{end.day})", "",
           f"채널 전체: 구독자 {int(ch['subscriberCount']):,}명 · 누적 조회수 {int(ch['viewCount']):,}회 · "
           f"공개 영상 {len(d['videos'])}개"]
    if d["week"]:
        w = d["week"]
        out.append(f"이번 주: 조회수 {w['views']:,}회 · 구독 +{w['gained']} / -{w['lost']}")
    out.append("")
    for k, name in KIND.items():
        vs = sorted([v for v in week if v["kind"] == k], key=lambda v: -v["views"])
        if not vs:
            continue
        avg_v = _avg([v["views"] for v in vs])
        avg_p = _avg([v["avg_pct"] for v in vs if "avg_pct" in v])
        out.append(f"■ {name} — {len(vs)}편 · 평균 {avg_v:,.0f}회"
                   + (f" · 평균 시청 비율 {avg_p:.0f}%" if avg_p is not None else ""))
        out += [_line(v) for v in vs]
        out.append("")
    if d["sources"]:
        total = sum(n for _s, n in d["sources"]) or 1
        out.append("■ 어디서 봤나 (이번 주 조회수)")
        out += [f"  {s} {n:,}회 ({n / total:.0%})" for s, n in d["sources"][:6]]
        out.append("")
    out.append("■ 이번 주 새 댓글" + ("" if d["comments"] else " — 없음"))
    out += [f"  {w.month}/{w.day} [{t[:30]}] {c}" + (" (답글 있음)" if n else " (답글 없음)")
            for w, t, c, n in d["comments"]]
    if d["analytics_error"]:
        out += ["", "※ 시청 비율·구독 증가·시청 경로는 빠졌습니다 — 유튜브 분석 API 를 쓸 수 없음:",
                f"  {d['analytics_error']}"]
    return "\n".join(out)


def mail(body: str, subject: str) -> str:
    env = _load_env()
    user, password = env.get("SMTP_USER", ""), env.get("SMTP_PASS", "")
    if not user or not password:
        return "SMTP_USER / SMTP_PASS 없음"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, user          # 동화님 본인에게만
    msg.set_content(body)
    with smtplib.SMTP_SSL(env.get("SMTP_HOST", "smtp.gmail.com"), int(env.get("SMTP_PORT", 465)), timeout=30) as s:
        s.login(user, password)
        s.send_message(msg)
    return ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    today = datetime.now(KST).date()
    d = collect(today)
    body = text(d)
    print(body)
    if "--mail" in sys.argv:
        problem = mail(body, f"[하차니] 유튜브 주간 성과 {d['start'].month}/{d['start'].day}~{d['end'].month}/{d['end'].day}")
        print("✗ 메일: " + problem if problem else "✓ 메일 보냄")
        return 1 if problem else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
