"""규칙 기반 해석기 — LLM 없이 돌아가는 브리핑의 본체.

여기서 만드는 예측은 전부 두 가지를 갖춰야 한다.
  1) 숫자 트리거   : "10Y가 4.95 위로 마감하면" — 다음날 자동 채점 가능
  2) 경험적 확률   : 과거 2년 데이터에서 같은 조건을 찾아 실제 빈도를 센 값

2번이 핵심이다. LLM에게 확률을 물으면 근거 없는 '느낌'이 나오지만,
여기서는 "과거 이 조건이 37번 있었고 그중 23번 그렇게 됐다 = 62%"라고
셀 수 있다. 틀려도 근거가 남는다.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from brief import db  # noqa: E402
from brief.collect.market import Instrument, load_instruments  # noqa: E402

MIN_SAMPLES = 12          # 이보다 사례가 적으면 확률을 말하지 않는다


# ─────────────────────────────────────────────────────────────
#  경험적 확률
# ─────────────────────────────────────────────────────────────

def base_rate(conn, instrument: str, where_sql: str, params: tuple,
              target_field: str, op: str, threshold: float,
              horizon: int = 1) -> tuple[float, int] | None:
    """과거에 같은 조건이 있었던 날들을 찾아, horizon일 뒤 결과의 빈도를 센다.

    반환: (확률, 표본수) — 표본이 MIN_SAMPLES 미만이면 None.
    """
    rows = conn.execute(
        f"SELECT trade_date FROM metrics WHERE instrument = ? AND {where_sql} "
        f"ORDER BY trade_date", (instrument, *params)).fetchall()
    if len(rows) < MIN_SAMPLES:
        return None

    dates = [r["trade_date"] for r in rows]
    hits = total = 0

    for d in dates:
        fut = conn.execute(
            "SELECT trade_date FROM observations WHERE instrument=? AND trade_date > ? "
            "ORDER BY trade_date LIMIT ?", (instrument, d, horizon)).fetchall()
        if len(fut) < horizon:
            continue
        target_date = fut[horizon - 1]["trade_date"]

        if target_field == "close":
            row = conn.execute(
                "SELECT close AS v FROM observations WHERE instrument=? AND trade_date=?",
                (instrument, target_date)).fetchone()
        else:
            row = conn.execute(
                f"SELECT {target_field} AS v FROM metrics WHERE instrument=? AND trade_date=?",
                (instrument, target_date)).fetchone()
        if not row or row["v"] is None:
            continue

        total += 1
        v = row["v"]
        if (op == ">" and v > threshold) or (op == "<" and v < threshold):
            hits += 1

    if total < MIN_SAMPLES:
        return None
    return hits / total, total


def move_probability(conn, instrument: str, required_pct: float,
                     direction: str, horizon: int = 1,
                     unit: str = "pct") -> tuple[float, int] | None:
    """'하루 만에 required_pct 만큼 움직일 확률'을 과거 수익률 분포에서 센다.

    라운드 레벨 돌파에는 이쪽을 쓴다. 절대 가격으로 세면
    "지난 2년간 코스피가 6,900 아래였던 비율" 같은 무의미한 값이 나오는데,
    지수는 추세가 있어서 과거 절대 수준은 내일과 아무 상관이 없기 때문이다.
    필요 변동률로 정규화해야 "내일 그만큼 움직일 수 있는가"라는 질문이 된다.
    """
    closes = [r["close"] for r in conn.execute(
        "SELECT close FROM observations WHERE instrument = ? ORDER BY trade_date",
        (instrument,)).fetchall()]
    if len(closes) < 60 + horizon:
        return None

    if unit == "bp":
        # 금리는 수준 대비 %가 아니라 bp 차이로 센다
        rets = [(closes[i + horizon] - closes[i]) * 100
                for i in range(len(closes) - horizon)]
    else:
        rets = [(closes[i + horizon] / closes[i] - 1) * 100
                for i in range(len(closes) - horizon)
                if closes[i]]
    if len(rets) < MIN_SAMPLES:
        return None

    if direction == "up":
        hits = sum(1 for r in rets if r >= required_pct)
    else:
        hits = sum(1 for r in rets if r <= required_pct)
    return hits / len(rets), len(rets)


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% 신뢰구간.

    0/16 을 '확률 0%'라고 말하면 거짓말에 가깝다. 실제로는 '0~19% 어딘가'가
    맞다. 표본이 적을 때 점추정을 그대로 내보내지 않기 위해 쓴다.
    """
    if n <= 0:
        return 0.0, 1.0
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def josa(word: str, pair: str = "이/가") -> str:
    """받침 유무에 따라 조사를 고른다. '환율가' 같은 어색한 문장을 막는다."""
    a, b = pair.split("/")
    if not word:
        return b
    last = word[-1]
    # 숫자로 끝나면 읽는 소리(영/일/삼/육/칠/팔/십/백/천 …)로 받침을 판단한다
    if last.isdigit():
        return a if last in "0136780" else b
    if not ("가" <= last <= "힣"):
        return b
    return a if (ord(last) - 0xAC00) % 28 else b


# ─────────────────────────────────────────────────────────────
#  라운드 넘버 — 시장이 실제로 의식하는 심리적 가격대
# ─────────────────────────────────────────────────────────────

def round_step(value: float) -> float:
    """값 크기의 1% 수준에서 가장 자연스러운 눈금(1/2/5 × 10^n)을 고른다."""
    if value <= 0:
        return 1.0
    rough = abs(value) * 0.01
    mag = 10 ** math.floor(math.log10(rough))
    for m in (1, 2, 5, 10):
        if rough <= m * mag:
            return m * mag
    return 10 * mag


def nearest_levels(value: float) -> tuple[float, float]:
    """현재값 바로 위/아래의 라운드 레벨."""
    step = round_step(value)
    below = math.floor(value / step) * step
    above = below + step
    return below, above


# ─────────────────────────────────────────────────────────────
#  트리거 후보 생성
# ─────────────────────────────────────────────────────────────

@dataclass
class Trigger:
    instrument: str
    name: str
    claim: str                 # 사람이 읽는 문장
    field: str
    op: str
    threshold: float
    horizon: int
    probability: float | None
    samples: int | None
    kind: str                  # round_level / band_edge / ma_cross / streak / sigma
    priority: float            # 정렬용. 클수록 위
    situation: str = ""        # 지금이 어떤 상황인지 한 줄. 조건문과 분리해 읽기 쉽게.

    def interval(self) -> tuple[float, float] | None:
        if self.probability is None or not self.samples:
            return None
        return wilson(round(self.probability * self.samples), self.samples)

    @property
    def is_uncertain(self) -> bool:
        """신뢰구간이 30%p 보다 넓으면 숫자 하나로 말하지 않는다."""
        iv = self.interval()
        return iv is None or (iv[1] - iv[0]) > 0.30

    def prob_label(self) -> str:
        """화면·카톡에 크게 보여줄 확률 표기. 불확실하면 범위로."""
        iv = self.interval()
        if iv is None:
            return "—"
        if self.is_uncertain:
            return f"{iv[0]:.0%}~{iv[1]:.0%}"
        return f"{self.probability:.0%}"

    def prob_text(self) -> str:
        if self.probability is None or self.samples is None:
            return "과거 사례가 부족해 확률을 제시하지 않습니다"

        hits = round(self.probability * self.samples)
        lo, hi = wilson(hits, self.samples)
        band = f"{lo:.0%}~{hi:.0%}"

        # 표본이 적으면 점추정이 과신이 된다. 0/16 을 '0%'라고 말하면 안 된다.
        if hi - lo > 0.30:
            return (f"지금까지 비슷한 상황이 {self.samples}번 있었고 그중 {hits}번 그랬습니다. "
                    f"횟수가 적어 {band} 사이라고만 말할 수 있습니다.")
        return (f"지금까지 비슷한 상황이 {self.samples}번 있었고 그중 {hits}번 그랬습니다"
                f"(나머지 {self.samples - hits}번은 아니었습니다). "
                f"횟수가 더 늘면 {band} 안에서 움직일 값입니다.")


def _fmt(v: float, inst: Instrument) -> str:
    return f"{v:,.{inst.decimals}f}"


def build_triggers(conn, rows, instruments: dict[str, Instrument],
                   cfg: dict) -> list[Trigger]:
    """각 지표의 최신 상태에서 '내일 지켜볼 조건'을 뽑아낸다.

    rows 는 지표별 최신 스냅샷이다. 하나의 날짜로 묶지 않는 이유는
    미국장이 한국보다 하루 늦게 마감해 거래일이 어긋나기 때문이다.
    """
    out: list[Trigger] = []

    for r in rows:
        iid = r["instrument"]
        inst = instruments.get(iid)
        if not inst or r["close"] is None or not inst.can_claim:
            continue

        close = r["close"]
        sigma = r["sigma"]
        p52 = r["pct_52w"]
        vs20 = r["vs_ma20"]
        streak = r["streak"] or 0

        # ── 1. 라운드 레벨 돌파 ──────────────────────────────
        below, above = nearest_levels(close)
        dist_up = (above - close) / close * 100
        dist_dn = (close - below) / close * 100

        # 금리는 필요한 움직임을 bp 로 계산하고 표시한다
        is_rate = inst.kind == "rate"
        unit = "bp" if is_rate else "pct"
        need_up = (above - close) * 100 if is_rate else dist_up
        need_dn = (close - below) * 100 if is_rate else dist_dn
        up_txt = f"+{need_up:.0f}bp 필요" if is_rate else f"+{dist_up:.2f}% 필요"
        dn_txt = f"−{need_dn:.0f}bp 필요" if is_rate else f"−{dist_dn:.2f}% 필요"

        if dist_up <= 1.2:
            br = move_probability(conn, iid, need_up, "up", 1, unit)
            out.append(Trigger(
                iid, inst.name,
                f"{inst.name}{josa(inst.name)} {_fmt(above, inst)} 위로 마감한다",
                "close", ">", above, 1,
                br[0] if br else None, br[1] if br else None,
                "round_level", 2.0 - dist_up,
                situation=f"지금 {_fmt(close, inst)} — 하루 만에 {up_txt.replace(' 필요', '')} "
                          f"오르면 넘어섭니다"))

        if dist_dn <= 1.2:
            br = move_probability(conn, iid, -need_dn, "down", 1, unit)
            out.append(Trigger(
                iid, inst.name,
                f"{inst.name}{josa(inst.name)} {_fmt(below, inst)} 아래로 마감한다",
                "close", "<", below, 1,
                br[0] if br else None, br[1] if br else None,
                "round_level", 2.0 - dist_dn,
                situation=f"지금 {_fmt(close, inst)} — 하루 만에 {dn_txt.replace(' 필요', '')} "
                          f"내리면 밑돕니다"))

        # ── 2. 52주 밴드 극단 ────────────────────────────────
        # 극단에 '머무는가'를 묻지 않는다 — 자기상관 때문에 거의 항상 맞아서
        # 적중률만 부풀리는 공짜 예측이 된다. '벗어나는가'를 물어야 예측이다.
        if p52 is not None and (p52 <= 8 or p52 >= 92):
            at_low = p52 <= 8
            edge = "최저" if at_low else "최고"
            op = ">" if at_low else "<"
            thr = 8.0 if at_low else 92.0
            br = base_rate(conn, iid,
                           "pct_52w IS NOT NULL AND pct_52w "
                           + ("<= 8" if at_low else ">= 92"),
                           (), "pct_52w", op, thr, 1)
            out.append(Trigger(
                iid, inst.name,
                f"{inst.name}{josa(inst.name)} {'바닥에서 올라선다' if at_low else '고점에서 내려온다'}",
                "pct_52w", op, thr, 1,
                br[0] if br else None, br[1] if br else None,
                "band_edge", 3.0,
                situation=f"지금 값이 최근 1년 사이 "
                          f"{'가장 낮았던 값 근처' if at_low else '가장 높았던 값 근처'}입니다"))

        # ── 3. 20일선 근접 (추세 전환 분기점) ────────────────
        if vs20 is not None and abs(vs20) <= 0.8:
            ma20 = r["ma20"]
            above_ma = vs20 >= 0
            op = ">" if above_ma else "<"
            # 지금 20일선 위에 있는지 아래에 있는지까지 조건에 넣어야
            # "같은 상황"의 과거 사례가 된다. 섞으면 평균으로 뭉개진다.
            br = base_rate(conn, iid,
                           "vs_ma20 IS NOT NULL AND ABS(vs_ma20) <= 0.8 AND vs_ma20 "
                           + (">= 0" if above_ma else "< 0"),
                           (), "vs_ma20", op, 0.0, 1)
            out.append(Trigger(
                iid, inst.name,
                f"{inst.name}{josa(inst.name)} 20일 평균선 "
                f"{'위를 지킨다' if above_ma else '아래에 머문다'}",
                "vs_ma20", op, 0.0, 1,
                br[0] if br else None, br[1] if br else None,
                "ma_cross", 2.5,
                situation=f"지금 최근 20일 평균값({_fmt(ma20, inst)}) "
                          f"{'바로 위' if above_ma else '바로 아래'}에 붙어 있습니다"))

        # ── 4. 연속 흐름 ─────────────────────────────────────
        if abs(streak) >= 4:
            direction = "상승" if streak > 0 else "하락"
            op = ">" if streak > 0 else "<"
            br = base_rate(conn, iid,
                           f"streak {'>=' if streak > 0 else '<='} {streak}", (),
                           "chg", op, 0.0, 1)
            out.append(Trigger(
                iid, inst.name,
                f"{inst.name}{josa(inst.name)} 하루 더 {direction}한다",
                "chg", op, 0.0, 1,
                br[0] if br else None, br[1] if br else None,
                "streak", 1.5 + abs(streak) * 0.1,
                situation=f"{abs(streak)}거래일 연속 {direction}했습니다"))

        # ── 5. 이례적 변동 뒤 되돌림 ─────────────────────────
        if sigma is not None and abs(sigma) >= cfg["sigma_notable"]:
            op = "<" if sigma > 0 else ">"
            br = base_rate(conn, iid,
                           f"sigma IS NOT NULL AND sigma {'>=' if sigma > 0 else '<='} "
                           f"{cfg['sigma_notable'] if sigma > 0 else -cfg['sigma_notable']}",
                           (), "chg", op, 0.0, 1)
            out.append(Trigger(
                iid, inst.name,
                f"{inst.name}{josa(inst.name)} 반대로 {'내린다' if sigma > 0 else '오른다'}",
                "chg", op, 0.0, 1,
                br[0] if br else None, br[1] if br else None,
                "sigma", 4.0 + abs(sigma),
                situation=f"직전 거래일에 평소보다 크게 {'올랐습니다' if sigma > 0 else '내렸습니다'}"
                          f" (평소 하루 변동폭의 {abs(sigma):.1f}배)"))

    out.sort(key=lambda t: -t.priority)
    return out


# ─────────────────────────────────────────────────────────────
#  지갑 단위 환산 — 비유 대신 쓰는 장치
# ─────────────────────────────────────────────────────────────

def pocket_translate(iid: str, close: float, chg: float,
                     chg_pct: float | None, chg_bp: float | None) -> str | None:
    """추상적 숫자를 생활 단위로 바꾼다.

    여기 들어가는 문장은 '누구나 다시 계산해 볼 수 있는 산수'여야 한다.
    반영 비율·연동 관계처럼 출처로 뒷받침할 수 없는 환산(유가→휘발유값,
    미 국채→국내 대출이자)은 그럴듯한 거짓이 되기 쉬워 두지 않는다.
    """
    if iid == "USDKRW" and abs(chg) >= 0.5:
        prev = close - chg
        # 어제 100만원으로 살 수 있던 달러를 오늘 사는 데 드는 돈의 차이
        diff = 1_000_000 * close / prev - 1_000_000
        dollars = 1_000_000 / prev
        more = "더" if diff > 0 else "덜"
        return (f"환율이 {prev:,.2f}원에서 {close:,.2f}원이 됐습니다. "
                f"어제 100만원으로 살 수 있던 {dollars:,.0f}달러를 오늘 사려면 "
                f"약 {abs(diff):,.0f}원이 {more} 듭니다(수수료 제외).")

    if iid == "VIX":
        band = close / math.sqrt(12)
        return (f"VIX {close:.1f}을 한 달 기준으로 환산하면 약 ±{band:.1f}%입니다"
                f"({close:.1f} ÷ √12). S&P500 옵션 가격에 반영된 한 달 예상 변동폭입니다.")

    return None


if __name__ == "__main__":
    import yaml
    root = Path(__file__).resolve().parent.parent.parent
    cfg = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8"))
    insts = {i.id: i for i in load_instruments()}

    with db.connect() as c:
        latest = c.execute("SELECT MAX(trade_date) d FROM metrics").fetchone()["d"]
        trigs = build_triggers(c, latest, insts, cfg["analysis"])

        print(f"=== {latest} 기준 · 내일 지켜볼 조건 {len(trigs)}개 ===\n")
        for t in trigs[:10]:
            print(f"[{t.kind}] {t.claim}")
            print(f"    → {t.prob_text()}\n")

        print("=== 지갑 단위 환산 ===")
        for r in c.execute(
                "SELECT m.*, o.close FROM metrics m JOIN observations o "
                "ON m.trade_date=o.trade_date AND m.instrument=o.instrument "
                "WHERE m.trade_date=?", (latest,)):
            txt = pocket_translate(r["instrument"], r["close"], r["chg"],
                                   r["chg_pct"], r["chg_bp"])
            if txt:
                print(f"  · {insts[r['instrument']].name}: {txt}")
