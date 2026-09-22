"""쇼츠 영상 만들기 — 1080×1920 세로, 캐릭터가 오른쪽 아래에서 브리핑한다.

  목소리    edge-tts (무료·비공식 — 동화님 결정). 구간마다 따로 만들어 길이를 잰다.
  입 모양   말하는 동안 talk_open(ㅇ)과 talk_closed(ㅡ)를 번갈아 — 소리 크기와 무관.
            (동화님: "두 개의 이미지가 번갈아 가며 실제로 말하는 것처럼 보이게만")
  대기      다음 이슈로 넘어가며 쉬는 구간은 smile(기본형).
  놀람      놀랄 만한 구간은 처음 SURPRISE_SEC 동안 surprised, 그다음부터 말하기.
  자켓      상승 빨강 / 하락 파랑 (대본의 mood).

프레임은 PIL 로 그려 ffmpeg 에 바로 흘려 넣는다. 구간마다 배경·글자 판을 한 번만 그리고
프레임마다 캐릭터만 얹어서 빠르다.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.shorts.script import Script  # noqa: E402

W, H = 1080, 1920
FPS = 15                     # 그리기는 15fps, 출력은 30fps 로 늘린다 (입 모양 전환엔 충분)
MOUTH_EVERY = 2              # 프레임 2장마다 입 모양을 바꾼다 = 약 0.13초
GAP_SEC = 0.55               # 이슈 사이 대기(웃는 얼굴)
LEAD_SEC = 0.35              # 영상 시작 전 대기
SURPRISE_SEC = 1.1
VOICE = "ko-KR-SunHiNeural"
RATE = "+8%"
CHAR_H = 700                 # 화면 위 캐릭터 높이
CARD_BOTTOM = 980            # 본문 카드 아래 끝
CHAR_BOTTOM = 40             # 캐릭터 발 아래 여백

CHAR_DIR = ROOT / "assets" / "character"
UP, DOWN, INK, DIM = (229, 72, 77), (59, 130, 246), (15, 23, 42), (100, 116, 139)
FONT_CANDIDATES = {
    "bold": ["C:/Windows/Fonts/malgunbd.ttf",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
             "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"],
    "regular": ["C:/Windows/Fonts/malgun.ttf",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"],
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES[kind]:
        if Path(p).exists():
            # .ttc 의 0번은 일본어 우선이라 한국어(KR) 번호를 고른다
            index = 1 if p.endswith(".ttc") else 0
            return ImageFont.truetype(p, size, index=index)
    raise FileNotFoundError("한글 글꼴을 찾지 못했습니다 (Linux 는 fonts-noto-cjk 설치 필요)")


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FileNotFoundError("ffmpeg 이 없습니다")
    return exe


def _duration(path: Path) -> float:
    out = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


# ── 소리 ────────────────────────────────────────────────────

async def _tts_all(texts: list[str], folder: Path) -> list[Path]:
    import edge_tts
    paths = []
    for i, t in enumerate(texts):
        p = folder / f"seg{i:02d}.mp3"
        await edge_tts.Communicate(t, VOICE, rate=RATE).save(str(p))
        paths.append(p)
    return paths


def _audio(script: Script, folder: Path) -> tuple[Path, list[tuple[float, float]]]:
    """구간별 목소리를 이어 붙인 소리 파일과, 구간마다 (말 시작, 말 끝) 시각."""
    clips = asyncio.run(_tts_all([s.speech for s in script.segments], folder))
    silence = {}
    for sec in (LEAD_SEC, GAP_SEC):
        p = folder / f"sil{int(sec * 1000)}.mp3"
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=24000:cl=mono", "-t", f"{sec}", "-c:a", "libmp3lame",
                        "-b:a", "48k", str(p)], check=True)
        silence[sec] = p
    order, spans, t = [silence[LEAD_SEC]], [], LEAD_SEC
    for i, c in enumerate(clips):
        d = _duration(c)
        spans.append((t, t + d))
        order.append(c)
        t += d
        if i < len(clips) - 1:
            order.append(silence[GAP_SEC])
            t += GAP_SEC
    lst = folder / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in order), encoding="utf-8")
    out = folder / "voice.m4a"
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c:a", "aac", "-b:a", "128k", str(out)], check=True)
    return out, spans


# ── 그림 ────────────────────────────────────────────────────

def _characters(mood: str) -> dict[str, Image.Image]:
    color = "red" if mood == "up" else "blue"
    out = {}
    for pose in ("smile", "talk_open", "talk_closed", "surprised"):
        im = Image.open(CHAR_DIR / f"{color}_{pose}.png").convert("RGBA")
        out[pose] = im.resize((round(im.width * CHAR_H / im.height), CHAR_H), Image.LANCZOS)
    return out


def _gradient() -> Image.Image:
    top, bottom = (15, 23, 42), (30, 41, 59)
    g = Image.new("RGB", (1, H))
    for y in range(H):
        k = y / (H - 1)
        g.putpixel((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * k) for i in range(3)))
    return g.resize((W, H)).convert("RGBA")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        test = f"{cur} {word}".strip()
        if draw.textlength(test, font=font) <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


NUM = re.compile(r"[+\-−]\d[\d,]*(?:\.\d+)?%?")


def _colored_line(draw: ImageDraw.ImageDraw, xy, text: str, font, base=INK):
    """숫자 부분만 부호에 따라 빨강(+)·파랑(-)으로."""
    x, y = xy
    pos = 0
    for m in NUM.finditer(text):
        if m.start() > pos:
            seg = text[pos:m.start()]
            draw.text((x, y), seg, font=font, fill=base)
            x += draw.textlength(seg, font=font)
        num = m.group()
        draw.text((x, y), num, font=font, fill=UP if num[0] == "+" else DOWN)
        x += draw.textlength(num, font=font)
        pos = m.end()
    if pos < len(text):
        draw.text((x, y), text[pos:], font=font, fill=base)


def _panel(script: Script, idx: int, base: Image.Image) -> Image.Image:
    """구간 하나의 배경 판 (캐릭터 빼고 전부)."""
    im = base.copy()
    d = ImageDraw.Draw(im)
    accent = UP if script.mood == "up" else DOWN
    seg = script.segments[idx]

    # 머리
    d.rounded_rectangle((60, 110, 60 + 330, 110 + 64), 32, fill=accent)
    d.text((60 + 165, 142), "매일 경제 브리핑", font=_font("bold", 32), fill="white", anchor="mm")
    d.text((60, 210), f"{script.date_label} {script.title}", font=_font("bold", 76), fill="white")

    # 진행 점
    body = [i for i, s in enumerate(script.segments) if s.kind not in ("intro", "outro")]
    for k, i in enumerate(body):
        on = i == idx
        cx = 60 + k * 44
        d.ellipse((cx, 340, cx + (26 if on else 18), 340 + (26 if on else 18)),
                  fill=accent if on else (71, 85, 105))

    # 본문 카드 — 카톡 줄 그대로. ' · ' 로 나뉜 항목을 한 줄씩.
    # 시작·끝 구간은 오늘 다룰 줄(카톡 줄)을 목록으로 보여 준다.
    d.rounded_rectangle((50, 420, W - 50, CARD_BOTTOM), 40, fill=(248, 250, 252))
    if seg.kind in ("intro", "outro"):
        head = "오늘 브리핑" if seg.kind == "intro" else seg.screen
        d.text((100, 470), head, font=_font("bold", 60), fill=accent)
        f = _font("bold", 38)
        y = 575
        for s in script.segments[1:-1]:
            for line in _wrap(d, s.screen, f, W - 200)[:2]:
                _colored_line(d, (100, y), line, f)
                y += 52
            y += 22
    else:
        items = [p.strip() for p in seg.screen.split("·")] if " · " in seg.screen else [seg.screen]
        longest = max(len(i) for i in items)
        size = 78 if longest <= 14 else 64 if longest <= 20 else 54
        f = _font("bold", size)
        y = 480
        for item in items:
            for line in _wrap(d, item, f, W - 180):
                _colored_line(d, (100, y), line, f)
                y += int(size * 1.35)
            y += int(size * 0.35)

    # 자막 — 카드 아래 한 줄 띠, 캐릭터 머리 위
    sub_f = _font("bold", 40)
    lines = _wrap(d, seg.speech, sub_f, W - 140)[:3]
    sy = CARD_BOTTOM + 30
    for line in lines:
        d.text((W // 2, sy), line, font=sub_f, fill="white", anchor="ma")
        sy += 56

    # 바닥 글
    d.text((60, H - 70), "공식 데이터 자동 집계", font=_font("regular", 30), fill=DIM)
    d.text((60, H - 115), "투자 권유 아님", font=_font("regular", 30), fill=DIM)
    return im


def _pose(t: float, spans: list[tuple[float, float]], segs, frame: int) -> str:
    for (a, b), s in zip(spans, segs):
        if a <= t < b:
            if s.surprise and t - a < SURPRISE_SEC:
                return "surprised"
            return "talk_open" if (frame // MOUTH_EVERY) % 2 == 0 else "talk_closed"
    return "smile"


def render(script: Script, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        audio, spans = _audio(script, folder)
        total = spans[-1][1] + 0.8
        chars = _characters(script.mood)
        base = _gradient()
        panels = [_panel(script, i, base) for i in range(len(script.segments))]

        # 구간 경계 — 대기 중에는 다음 구간 판을 미리 보여 준다
        def panel_at(t: float) -> Image.Image:
            for i, (a, b) in enumerate(spans):
                if t < b:
                    return panels[i]
            return panels[-1]

        proc = subprocess.Popen(
            [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(audio),
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-r", "30", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
             str(out)], stdin=subprocess.PIPE)
        n = int(total * FPS)
        for fr in range(n):
            t = fr / FPS
            frame = panel_at(t).copy()
            ch = chars[_pose(t, spans, script.segments, fr)]
            frame.alpha_composite(ch, (W - ch.width - 10, H - ch.height - CHAR_BOTTOM))
            proc.stdin.write(frame.convert("RGB").tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg 영상 만들기 실패")
    return out


SHORTS_DIR = ROOT / "data" / "shorts"


def make(payload: dict, message: str, weekly: bool = False, link: str | None = None) -> dict | None:
    """카톡 메시지로 대본을 만들고 영상까지. 올릴 때 쓸 제목·설명과 함께 돌려준다."""
    from brief.shorts.script import build
    script = build(payload, message, weekly=weekly, link=link or "")
    if script is None:
        return None
    out = render(script, SHORTS_DIR / f"{payload['brief_date']}.mp4")
    title = f"{script.date_label} {script.title} — " + script.segments[1].screen
    desc = "\n".join([f"{script.date_label} {script.title}", "",
                      *[s.screen for s in script.segments[1:-1]], "",
                      f"전체 브리핑: {link}" if link else "",
                      "공식 데이터(한국거래소·연준 등)를 자동으로 모아 만든 영상입니다. 투자 권유가 아닙니다.",
                      "#경제 #주식 #코스피 #Shorts"])
    return {"path": str(out), "title": title[:100], "description": desc,
            "mood": script.mood, "speech": script.text}
