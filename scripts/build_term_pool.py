"""경제 용어 쇼츠의 원천 — 공식 용어사전 설명을 모아 data/term_pool.json 으로 (10/5).

  python scripts/build_term_pool.py

  · 금융위원회 금융용어사전 (fsc.go.kr/in090301, 229건 — 공공데이터포털 15160317 '이용허락범위 제한 없음')
  · 한국은행 ECOS 통계용어사전 (StatisticWord API, ECOS_API_KEY) — 아래 WORDS 중 사전에 그대로 있는 것

쇼츠 대본(data/terms.json)은 이 설명에 있는 사실만으로 쓴다. 키 값은 출력하지 않는다.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "term_pool.json"
UA = {"User-Agent": "Mozilla/5.0 (daily-brief term pool)"}

# 일상에서 자주 듣는 경제·금융 낱말 — ECOS 사전에 '그대로' 있는 것만 남는다
WORDS = """기준금리 환율 인플레이션 디플레이션 소비자물가지수 생산자물가지수 경상수지 무역수지 국내총생산(GDP) 국민총소득(GNI)
주가지수 채권 금리 장단기금리차 외환보유액 통화량 실질금리 명목금리 국채 회사채 지방채 콜금리 양도성예금증서 CD금리
코픽스 예금금리 대출금리 가계부채 가계신용 국가채무 재정수지 관리재정수지 통합재정수지 경기선행지수 경기동행지수
경제성장률 잠재성장률 실업률 고용률 경제활동참가율 소비자심리지수 기업경기실사지수 수출물가지수 수입물가지수 교역조건
환율제도 변동환율제도 고정환율제도 평가절하 평가절상 기축통화 외환시장 외평채 국제수지 자본수지 금융계정 서비스수지
본원소득수지 이전소득수지 상품수지 통화정책 재정정책 공개시장운영 지급준비금 지급준비율 본원통화 광의통화(M2)
협의통화(M1) 신용창조 유동성 통화승수 화폐유통속도 물가안정목표 기대인플레이션 근원인플레이션 스태그플레이션
환매조건부채권 RP 기업어음 전자단기사채 신용평가 신용등급 부도 디폴트 모라토리엄 국가신용등급 신용부도스와프 CDS
가산금리 고정금리 변동금리 복리 단리 원리금균등상환 거치기간 담보대출 신용대출 주택담보대출 LTV DTI DSR 총부채상환비율
주식 배당 배당수익률 주가수익비율 PER 주가순자산비율 PBR 시가총액 유상증자 무상증자 액면분할 자사주 공매도 대주거래
증거금 신용거래 반대매매 상장 상장폐지 코스닥 코넥스 펀드 상장지수펀드 ETF 인덱스펀드 파생상품 선물 옵션 스와프
금리스와프 통화스와프 헤지 레버리지 디레버리징 버블 리먼사태 금융위기 뱅크런 예금보험 예금자보호제도 국제결제은행 BIS
BIS자기자본비율 스트레스테스트 그림자금융 핀테크 가상자산 중앙은행디지털화폐 CBDC 전자지급 간편결제 지급결제""".split()


def fsc() -> list[dict]:
    out = []
    for page in range(1, 40):
        h = requests.get(f"https://www.fsc.go.kr/in090301?curPage={page}", headers=UA, timeout=30).text
        items = re.findall(r'<a href="\./in090301/view\?dicId=(\d+)[^"]*"[^>]*>(.*?)</a>\s*</div>\s*'
                           r'<div class="info2">(.*?)</div>', h, re.S)
        if not items:
            break
        for dic, word, body in items:
            text = html.unescape(re.sub(r"<[^>]+>", " ", body))
            text = re.sub(r"\s+", " ", text).strip()
            out.append({"word": html.unescape(word).strip(), "definition": text, "source": "금융위원회 금융용어사전",
                        "url": f"https://www.fsc.go.kr/in090301/view?dicId={dic}"})
        time.sleep(0.4)
    return out


def ecos() -> list[dict]:
    from brief.collect.macro import _load_env
    key = _load_env().get("ECOS_API_KEY", "")
    out = []
    for w in WORDS:
        r = requests.get(f"https://ecos.bok.or.kr/api/StatisticWord/{key}/json/kr/1/5/{urllib.parse.quote(w)}",
                         timeout=30).json()
        rows = (r.get("StatisticWord") or {}).get("row") or []
        ex = next((x for x in rows if x["WORD"].strip() == w), None)
        if ex:
            out.append({"word": w, "definition": re.sub(r"\s+", " ", ex["CONTENT"]).strip(),
                        "source": "한국은행 경제통계시스템(ECOS) 통계용어사전", "url": "https://ecos.bok.or.kr"})
        time.sleep(0.2)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    pool = ecos() + fsc()
    seen, uniq = set(), []
    for p in pool:
        k = p["word"].replace(" ", "")
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    OUT.write_text(json.dumps({"_설명": "공식 용어사전 설명 모음 — scripts/build_term_pool.py 로 만든다. "
                                       "쇼츠 대본은 이 설명에 있는 사실만으로 쓴다.",
                               "terms": uniq}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ECOS {sum(p['source'].startswith('한국은행') for p in uniq)} · 금융위 "
          f"{sum(p['source'].startswith('금융위') for p in uniq)} → {OUT}")
