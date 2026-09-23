"""쇼츠 영상 만들기 — 1080×1920 세로, 캐릭터가 오른쪽 아래에서 브리핑한다.

  목소리    edge-tts (무료·비공식 — 동화님 결정). 귀여운 쪽으로 음 높이·빠르기를 올린다
            (9/22 "아나운서처럼 또박또박 안 해도 된다, 캐릭터에 어울리게").
            문장마다 따로 만들어 길이를 재고, 말풍선에는 지금 읽는 문장만 띄운다.
  입 모양   말하는 동안 talk_open(ㅇ)과 talk_closed(ㅡ)를 번갈아 — 소리 크기와 무관.
  대기      다음 이슈로 넘어가며 쉬는 구간은 smile(기본형).
  놀람      놀랄 만한 구간은 처음 SURPRISE_SEC 동안 surprised, 그다음부터 말하기.
  자켓      상승 빨강 / 하락 파랑 (대본의 mood).
  화면      파스텔 바탕 + 스티커 같은 카드, 둥근 글꼴(주아)과 손글씨 말풍선(개구).
            웹 리포트와 다른 글꼴로 — "캡처해서 따온 것 같다"(9/22)는 말에 따라.
  길이      쇼츠 상한(3분) 안에 들도록 MAX_SEC 를 넘으면 덜 중요한 구간부터 뺀다.

프레임은 (문장, 표정)마다 한 번만 그려 두고 되풀이해 흘려보낸다.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from brief.shorts.script import Script, Segment  # noqa: E402

W, H = 1080, 1920
FPS = 15                     # 그리기는 15fps, 출력은 30fps 로 늘린다 (입 모양 전환엔 충분)
MOUTH_EVERY = 2              # 프레임 2장마다 입 모양을 바꾼다 = 약 0.13초
GAP_SEC = 0.55               # 이슈 사이 대기(웃는 얼굴)
SENT_GAP_SEC = 0.12          # 한 이슈 안 문장 사이
LEAD_SEC = 0.35
TAIL_SEC = 0.8
SURPRISE_SEC = 1.1
MAX_SEC = 170                # 쇼츠 상한 180초에 여유를 둔다

VOICE = {"voice": "ko-KR-SunHiNeural", "rate": "+15%", "pitch": "+45Hz"}

CHAR_H = 640
CHAR_BOTTOM = 30
CARD_TOP = 380              # 이름표(카드 위 32px)가 진행 점(y 300)에 닿지 않게 (9/23)
BUBBLE_MAX_BOTTOM = 1225     # 말풍선 아래 끝 (캐릭터 머리 위)
ROW_H = 132                  # 카드 한 줄 높이

CHAR_DIR = ROOT / "assets" / "character"
FONT_DIR = ROOT / "assets" / "fonts"
INK = (58, 52, 50)
SOFT = (125, 116, 112)
CREAM = (255, 253, 247)
THEME = {   # 바탕 위·아래, 강조색
    "up":   {"top": (255, 244, 240), "bottom": (255, 226, 219), "accent": (240, 100, 110)},
    "down": {"top": (238, 246, 255), "bottom": (218, 234, 255), "accent": (79, 142, 247)},
}
UP_C, DOWN_C = (232, 72, 85), (52, 120, 230)


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    name = {"round": "Jua-Regular.ttf", "hand": "Gaegu-Bold.ttf"}[kind]
    return ImageFont.truetype(str(FONT_DIR / name), size)


def _t(text: str) -> str:
    """주아·개구 글꼴에는 가운뎃점(·)이 없어 네모로 나온다 — 쉼표·빗금으로 바꿔 그린다."""
    return text.replace(" · ", ", ").replace("·", "/")


def _tool(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise FileNotFoundError(f"{name} 이 없습니다")
    return exe


def _duration(path: Path) -> float:
    out = subprocess.run([_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


# ── 소리 ────────────────────────────────────────────────────

@dataclass
class Line:
    seg: int              # 구간 번호
    text: str             # 문장
    clip: Path
    dur: float = 0.0
    start: float = 0.0
    pauses: tuple = ()    # 문장 안에서 쉬는 구간 (문장 시작 기준 초) — 이때는 입을 닫는다

    @property
    def end(self) -> float:
        return self.start + self.dur


async def _tts(jobs: list[tuple[str, Path]], voice: dict) -> None:
    import edge_tts
    for text, path in jobs:
        for attempt in range(3):
            try:
                await edge_tts.Communicate(text, voice["voice"], rate=voice["rate"],
                                           pitch=voice["pitch"]).save(str(path))
                break
            except Exception:                              # noqa: BLE001
                if attempt == 2:
                    raise
                await asyncio.sleep(3)


def _voice_lines(script: Script, folder: Path, voice: dict) -> list[Line]:
    lines = [Line(i, text, folder / f"s{i:02d}_{k:02d}.mp3")
             for i, seg in enumerate(script.segments)
             for k, text in enumerate(sentences(seg.speech))]
    asyncio.run(_tts([(ln.text, ln.clip) for ln in lines], voice))
    for ln in lines:
        _trim(ln)
    return lines


SILENCE_DB = -45        # 이보다 작은 소리는 말이 아닌 것으로 본다
PAUSE_MIN = 0.15        # 이보다 긴 무음만 '쉼'으로 친다


def _trim(ln: Line) -> None:
    """읽어 주기 음성의 앞뒤 무음을 잘라 내고, 문장 안 쉼(쉼표 등)을 기록한다.

    edge-tts 음성은 앞에 약 0.15초, 끝에 약 0.34초 무음이 붙어 있다. 자르지 않으면 말이
    끝난 뒤에도 입이 움직였다 (9/23 동화님: "말이 끝나자마자 칼같이 입을 닫고 웃는 모습으로").
    """
    total = _duration(ln.clip)
    err = subprocess.run([_tool("ffmpeg"), "-hide_banner", "-i", str(ln.clip), "-af",
                          f"silencedetect=noise={SILENCE_DB}dB:d=0.05", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    spans = list(zip(starts, ends + [total] * (len(starts) - len(ends))))
    head = spans[0][1] if spans and spans[0][0] <= 0.01 else 0.0
    tail = spans[-1][0] if spans and spans[-1][1] >= total - 0.01 and spans[-1][0] > head else total
    inner = [(a - head, b - head) for a, b in spans
             if a > head + 0.01 and b < tail - 0.01 and b - a >= PAUSE_MIN]
    if head > 0 or tail < total:
        cut = ln.clip.with_name(ln.clip.stem + "_t.mp3")
        subprocess.run([_tool("ffmpeg"), "-y", "-loglevel", "error", "-i", str(ln.clip),
                        "-ss", f"{head:.3f}", "-to", f"{tail:.3f}", "-c:a", "libmp3lame",
                        "-b:a", "96k", str(cut)], check=True)
        ln.clip = cut
    ln.dur = tail - head
    ln.pauses = tuple(inner)


def _place(lines: list[Line]) -> float:
    """문장 시작 시각을 정하고 전체 길이를 돌려준다."""
    t = LEAD_SEC
    for j, ln in enumerate(lines):
        if j:
            t += GAP_SEC if ln.seg != lines[j - 1].seg else SENT_GAP_SEC
        ln.start = t
        t += ln.dur
    return t + TAIL_SEC


def _fit(script: Script, lines: list[Line]) -> tuple[list[int], list[Line]]:
    """MAX_SEC 안에 들 때까지 덜 중요한(priority 큰) 구간을 뒤에서부터 뺀다."""
    keep = list(range(len(script.segments)))
    while _place([l for l in lines if l.seg in keep]) > MAX_SEC:
        cands = [i for i in keep if script.segments[i].priority > 1]
        if not cands:
            break
        keep.remove(max(cands, key=lambda i: (script.segments[i].priority, i)))
    kept = [l for l in lines if l.seg in keep]
    _place(kept)
    return keep, kept


def _audio(lines: list[Line], folder: Path) -> Path:
    """문장 소리를 제 시각에 놓아 한 파일로."""
    total = lines[-1].end + TAIL_SEC
    inputs, filters = [], []
    for j, ln in enumerate(lines):
        inputs += ["-i", str(ln.clip)]
        ms = int(ln.start * 1000)
        filters.append(f"[{j}:a]adelay={ms}|{ms}[a{j}]")
    mix = "".join(f"[a{j}]" for j in range(len(lines)))
    graph = ";".join(filters) + f";{mix}amix=inputs={len(lines)}:normalize=0,apad[out]"
    graph_file = folder / "mix.txt"
    graph_file.write_text(graph, encoding="utf-8")
    out = folder / "voice.m4a"
    subprocess.run([_tool("ffmpeg"), "-y", "-loglevel", "error", *inputs,
                    "-filter_complex_script", str(graph_file), "-map", "[out]",
                    "-t", f"{total:.2f}", "-c:a", "aac", "-b:a", "128k", str(out)], check=True)
    return out


# ── 그림 ────────────────────────────────────────────────────

def _characters(mood: str) -> dict[str, Image.Image]:
    color = "red" if mood == "up" else "blue"
    out = {}
    for pose in ("smile", "talk_open", "talk_closed", "surprised"):
        im = Image.open(CHAR_DIR / f"{color}_{pose}.png").convert("RGBA")
        out[pose] = im.resize((round(im.width * CHAR_H / im.height), CHAR_H), Image.LANCZOS)
    return out


def _background(mood: str) -> Image.Image:
    th = THEME[mood]
    g = Image.new("RGB", (1, H))
    for y in range(H):
        k = y / (H - 1)
        g.putpixel((0, y), tuple(round(th["top"][i] + (th["bottom"][i] - th["top"][i]) * k)
                                 for i in range(3)))
    im = g.resize((W, H)).convert("RGBA")
    dots = Image.new("RGBA", (W, H), (0, 0, 0, 0))      # 옅은 물방울 무늬
    d = ImageDraw.Draw(dots)
    for row, y in enumerate(range(40, H, 150)):
        for x in range(40 + (75 if row % 2 else 0), W, 150):
            d.ellipse((x, y, x + 16, y + 16), fill=(*th["accent"], 28))
    im.alpha_composite(dots)
    return im


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> list[str]:
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


def _fit_font(draw, text: str, kind: str, size: int, width: float, min_size: int = 30):
    while size > min_size and draw.textlength(text, font=_font(kind, size)) > width:
        size -= 2
    return _font(kind, size)


def _value_color(v: str):
    v = v.strip()
    if v.startswith("+"):
        return UP_C
    if v.startswith(("-", "−")):
        return DOWN_C
    return INK


def _sticker(im: Image.Image, box, accent, radius=44):
    """테두리 굵은 크림색 카드 + 비낀 그림자 — 스티커 느낌."""
    x0, y0, x1, y1 = box
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0 + 12, y0 + 14, x1 + 12, y1 + 14), radius,
                                             fill=(*accent, 110))
    im.alpha_composite(shadow)
    ImageDraw.Draw(im).rounded_rectangle(box, radius, fill=CREAM, outline=INK, width=6)


def _header(im: Image.Image, script: Script, idx: int, keep: list[int]):
    d = ImageDraw.Draw(im)
    accent = THEME[script.mood]["accent"]
    f = _font("round", 36)
    label = "매일 경제 브리핑"
    w = d.textlength(label, font=f)
    d.rounded_rectangle((60, 100, 60 + w + 56, 164), 32, fill=accent)
    d.text((60 + 28, 132), label, font=f, fill="white", anchor="lm")
    d.text((60, 190), f"{script.date_label} {script.title}", font=_font("round", 80), fill=INK)
    body = [i for i in keep if script.segments[i].kind not in ("intro", "outro")]
    for k, i in enumerate(body):
        on = i == idx
        cx, cy, r = 72 + k * 40, 300, (13 if on else 8)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=accent if on else (200, 190, 186))


def _card(im: Image.Image, script: Script, idx: int, keep: list[int]) -> int:
    """구간 카드를 그리고 카드 아래 끝 y 를 돌려준다."""
    seg = script.segments[idx]
    accent = THEME[script.mood]["accent"]
    d = ImageDraw.Draw(im)
    x0, x1 = 50, W - 62
    inner = x1 - x0 - 100

    if seg.kind in ("intro", "outro"):
        head = "오늘 이야기할 것" if seg.kind == "intro" else seg.note
        chips = [_t(script.segments[i].tag) for i in keep if script.segments[i].tag]
        f = _font("round", 40)
        rows, cur, cw = [], [], 0
        for c in chips:
            w = d.textlength(c, font=f) + 50
            if cur and cw + w > inner:
                rows.append(cur)
                cur, cw = [], 0
            cur.append((c, w))
            cw += w + 16
        if cur:
            rows.append(cur)
        y1 = CARD_TOP + 150 + len(rows) * 76 + 30
        _sticker(im, (x0, CARD_TOP, x1, y1), accent)
        d.text((x0 + 50, CARD_TOP + 50), head, font=_fit_font(d, head, "round", 64, inner), fill=INK)
        y = CARD_TOP + 150
        for row in rows:
            x = x0 + 50
            for c, w in row:
                d.rounded_rectangle((x, y, x + w, y + 60), 30, fill=accent)
                d.text((x + w / 2, y + 30), c, font=f, fill="white", anchor="mm")
                x += w + 16
            y += 76
        return y1

    if seg.rows:
        y1 = CARD_TOP + 90 + len(seg.rows) * ROW_H + (84 if seg.note else 20)
    else:                                              # 오늘 한 줄 — 글만
        f = _font("round", 62)
        lines = _wrap(d, _t(seg.note), f, inner)
        y1 = CARD_TOP + 100 + len(lines) * 86 + 30
    _sticker(im, (x0, CARD_TOP, x1, y1), accent)

    # 이름표 — 카드 위 가장자리에 붙인 테이프처럼
    tf = _font("round", 42)
    tag = _t(seg.tag)
    tw = d.textlength(tag, font=tf)
    d.rounded_rectangle((x0 + 40, CARD_TOP - 32, x0 + 40 + tw + 60, CARD_TOP + 40), 24,
                        fill=accent, outline=INK, width=5)
    d.text((x0 + 70, CARD_TOP + 4), tag, font=tf, fill="white", anchor="lm")

    if not seg.rows:
        y = CARD_TOP + 90
        for line in lines:
            d.text((x0 + 50, y), line, font=f, fill=INK)
            y += 86
        return y1

    y = CARD_TOP + 70
    for k, (label, value) in enumerate(seg.rows):
        label, value = _t(label), _t(value)
        vf = _fit_font(d, value, "round", 88, inner * 0.55)
        vw = d.textlength(value, font=vf)
        lf = _fit_font(d, label, "round", 64, inner - vw - 30)
        d.text((x0 + 50, y + ROW_H / 2), label, font=lf, fill=INK, anchor="lm")
        d.text((x1 - 50, y + ROW_H / 2), value, font=vf, fill=_value_color(value), anchor="rm")
        if k < len(seg.rows) - 1:                      # 점선
            for x in range(x0 + 50, x1 - 60, 26):
                d.line((x, y + ROW_H, x + 12, y + ROW_H), fill=(210, 200, 196), width=3)
        y += ROW_H
    if seg.note:
        note = _t(seg.note)
        nf = _fit_font(d, note, "round", 40, inner)
        d.text((x0 + 50, y + 18), note, font=nf, fill=SOFT)
    return y1


def _bubble(im: Image.Image, text: str, card_bottom: int, tail_x: int):
    """손글씨 말풍선. 캐릭터 머리 바로 위에 붙이고, 꼬리는 머리 쪽으로."""
    d = ImageDraw.Draw(im)
    text = _t(text)
    x0, x1 = 50, W - 50
    room = BUBBLE_MAX_BOTTOM - card_bottom - 60        # 카드와 사이 60
    size = 64
    while True:
        f = _font("hand", size)
        lines = _wrap(d, text, f, x1 - x0 - 80)
        lh = int(size * 1.15)
        h = len(lines) * lh + 56
        if h <= room or size <= 40:
            break
        size -= 2
    y1 = BUBBLE_MAX_BOTTOM
    top = y1 - h
    d.rounded_rectangle((x0, top, x1, y1), 40, fill="white", outline=INK, width=5)
    # 꼬리 — 흰 삼각형을 말풍선 테두리(안쪽 5px)보다 위에서 시작해 테두리를 덮는다.
    # 테두리 선이 꼬리 입구를 가로질러 보이던 것을 없앤다 (9/23 동화님).
    tip = (tail_x + 16, y1 + 50)
    left, right = (tail_x - 34, y1 - 5), (tail_x + 30, y1 - 5)
    d.polygon([(left[0], y1 - 9), (right[0], y1 - 9), tip], fill="white")
    d.line((left, tip), fill=INK, width=5)
    d.line((right, tip), fill=INK, width=5)
    y = top + 24
    for line in lines:
        d.text((x0 + 40, y), line, font=f, fill=INK)
        y += lh


def _pose(t: float, lines: list[Line], segs: list[Segment], frame: int) -> str:
    first_start: dict[int, float] = {}
    for ln in lines:
        first_start.setdefault(ln.seg, ln.start)
    for ln in lines:
        if ln.start <= t < ln.end:
            if segs[ln.seg].surprise and t - first_start[ln.seg] < SURPRISE_SEC:
                return "surprised"
            if any(a <= t - ln.start < b for a, b in ln.pauses):
                return "talk_closed"                     # 쉼표에서 쉬는 동안은 입을 닫는다
            return "talk_open" if (frame // MOUTH_EVERY) % 2 == 0 else "talk_closed"
    return "smile"


def _line_at(t: float, lines: list[Line]) -> int:
    """지금 화면에 띄울 문장. 쉬는 동안엔 방금 끝난 문장을 두고,
    다음 구간 시작 GAP 절반 전부터 다음 문장을 보여 준다."""
    for j, ln in enumerate(lines):
        if t < ln.end:
            if j and t < ln.start - GAP_SEC / 2 and lines[j - 1].seg != ln.seg:
                return j - 1
            return j
    return len(lines) - 1


def render(script: Script, out: Path, voice: dict | None = None) -> tuple[Path, list[int]]:
    """영상을 만들고 (파일, 실제로 넣은 구간 번호)를 돌려준다."""
    out.parent.mkdir(parents=True, exist_ok=True)
    voice = {**VOICE, **(voice or {})}
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        keep, lines = _fit(script, _voice_lines(script, folder, voice))
        audio = _audio(lines, folder)
        total = lines[-1].end + TAIL_SEC
        chars = _characters(script.mood)
        base = _background(script.mood)
        cx = W - chars["smile"].width - 10
        cy = H - CHAR_H - CHAR_BOTTOM
        tail_x = cx + chars["smile"].width // 2 - 40

        panels: dict[int, tuple[Image.Image, int]] = {}
        for i in keep:
            im = base.copy()
            _header(im, script, i, keep)
            panels[i] = (im, _card(im, script, i, keep))
        cache: dict[tuple[int, str], bytes] = {}

        def frame_bytes(j: int, pose: str) -> bytes:
            if (j, pose) not in cache:
                im, bottom = panels[lines[j].seg]
                im = im.copy()
                _bubble(im, lines[j].text, bottom, tail_x)
                im.alpha_composite(chars[pose], (cx, cy))
                cache[(j, pose)] = im.convert("RGB").tobytes()
            return cache[(j, pose)]

        proc = subprocess.Popen(
            [_tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(audio),
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-r", "30", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
             str(out)], stdin=subprocess.PIPE)
        for fr in range(int(total * FPS)):
            t = fr / FPS
            proc.stdin.write(frame_bytes(_line_at(t, lines), _pose(t, lines, script.segments, fr)))
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg 영상 만들기 실패")
    return out, keep


SHORTS_DIR = ROOT / "data" / "shorts"


def make(payload: dict, message: str = "", weekly: bool = False, link: str | None = None,
         voice: dict | None = None) -> dict | None:
    """대본을 만들고 영상까지. 올릴 때 쓸 제목·설명과 함께 돌려준다."""
    from brief.shorts.script import build
    script = build(payload, message, weekly=weekly, link=link or "")
    if script is None:
        return None
    if voice is None:
        try:
            import yaml
            cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
            voice = (cfg.get("shorts") or {}).get("voice") or {}
        except Exception:                                  # noqa: BLE001
            voice = {}
    out, keep = render(script, SHORTS_DIR / f"{payload['brief_date']}.mp4", voice)
    used = [script.segments[i] for i in keep if script.segments[i].kind not in ("intro", "outro")]
    headline = next((s.screen for s in used if s.kind in ("kr", "week")),
                    used[0].screen if used else "")
    title = f"{script.date_label} {script.title} | {headline}"
    desc = "\n".join([f"{script.date_label} {script.title}", "",
                      *[f"[{s.tag}] {s.screen}" for s in used], "",
                      # 링크·면책 문구는 채널 설명에 있다 — 영상마다 넣지 않는다 (9/23 동화님)
                      "#경제 #주식 #코스피 #Shorts"])
    return {"path": str(out), "title": title[:100], "description": desc,
            "mood": script.mood, "speech": " ".join(script.segments[i].speech for i in keep)}
