"""캐릭터 이미지 8장을 쇼츠용 투명 PNG 로 맞춘다.

  빨간 자켓 4장 — 흰 배경. 가장자리에 이어진 흰색만 투명하게 한다(셔츠의 흰 부분은
    가장자리와 이어져 있지 않아 남는다). 오른쪽 아래 옅은 반짝이 표시는 캐릭터와
    떨어진 작은 덩어리라, 가장 큰 덩어리만 남기면 함께 빠진다.
  파란 자켓 4장 — 이미 투명. 안쪽 구멍(웃는 얼굴의 셔츠 자리)은 흰색으로 메운다.
  둘 다 캐릭터 둘레로 잘라 같은 높이로 맞춘다 — 자켓 색이 바뀌어도 크기가 튀지 않게.

  python scripts/prep_character.py
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "character" / "raw"
OUT = ROOT / "assets" / "character"
HEIGHT = 900          # 결과 높이(px). 쇼츠 화면(1920) 오른쪽 아래에 절반 크기로 얹는다.


def cut_white(im: Image.Image) -> Image.Image:
    a = np.asarray(im.convert("RGB")).astype(int)
    whiteish = (a.min(axis=2) >= 232) & (a.max(axis=2) - a.min(axis=2) <= 18)
    lab, _ = ndimage.label(whiteish)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(border))
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    rgba = np.dstack([a.astype(np.uint8), alpha])
    return Image.fromarray(rgba, "RGBA")


def keep_largest(im: Image.Image) -> Image.Image:
    arr = np.asarray(im).copy()
    mask = arr[..., 3] > 0
    lab, n = ndimage.label(mask)
    if n > 1:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        arr[..., 3][lab != (int(np.argmax(sizes)) + 1)] = 0
    # 가장자리 한 줄을 살짝 부드럽게 — 흰 테두리가 남지 않게
    alpha = Image.fromarray(arr[..., 3]).filter(__import__("PIL.ImageFilter").ImageFilter.MinFilter(3))
    arr[..., 3] = np.asarray(alpha)
    return Image.fromarray(arr, "RGBA")


def fill_holes(im: Image.Image) -> Image.Image:
    """캐릭터 안쪽의 투명·반투명 부분을 흰 바탕에 합쳐 불투명하게 만든다.

    파란 자켓 웃는 얼굴 원본은 셔츠 자리가 반투명(불투명도 약 27%)이라 어두운 배경에서
    셔츠가 어둡게 비쳤다. 바깥 테두리(배경에서 4px 안쪽까지)는 건드리지 않는다 —
    거기까지 흰색과 합치면 어두운 배경에서 흰 테두리가 생긴다.
    """
    arr = np.asarray(im).astype(float)
    a = arr[..., 3] / 255
    clear = a == 0
    lab, _ = ndimage.label(clear)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(border))
    near_bg = ndimage.binary_dilation(bg, iterations=4)
    inside = ~near_bg & (a < 1)
    for c in range(3):
        arr[..., c][inside] = arr[..., c][inside] * a[inside] + 255 * (1 - a[inside])
    arr[..., 3][inside] = 255
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def trim_scale(im: Image.Image) -> Image.Image:
    box = im.getbbox()
    im = im.crop(box)
    w = round(im.width * HEIGHT / im.height)
    return im.resize((w, HEIGHT), Image.LANCZOS)


for f in sorted(RAW.glob("*.webp")):
    im = Image.open(f).convert("RGBA")
    if f.stem.startswith("red_"):
        im = cut_white(im)
    im = trim_scale(fill_holes(keep_largest(im)))
    im.save(OUT / f"{f.stem}.png")
    print(f"{f.stem:<18} → {im.size}")
