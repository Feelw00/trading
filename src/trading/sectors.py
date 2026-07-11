"""섹터 분류(grounded·결정론) — DART 회사개황 업종코드(KSIC) → 26 taxonomy.

스크리너 후보의 섹터 태그를 채운다. **LLM 미개입**: DART가 준 등록업종(induty_code)을
커밋된 크로스워크로 매핑하는 순수 코드(CLAUDE.md "판단엔 LLM 미개입" 부합).

설계 근거(왜 전(全)코드 매핑이 아니라 보수적 채택인가):
- DART induty_code는 *법적 등록업종*이라 다각화 대형주·테마와 어긋난다.
  실측 예: 삼성전자=264(통신·방송장비), 라이콤(광부품)도 264 → 264는 반도체/통신 혼재.
- 그래서 기억으로 KSIC 의미를 추측하지 않고, **기존 ``llm-cls-v1`` 293라벨을 정답지로
  induty_code별 순도(purity)를 실측**(2026-06-09)해 **깨끗한 코드만** 결정론 규칙으로 채택했다.
  혼재 코드(264·262·292·201·649 등)는 미채택 → 미분류 유지(환각가드: 추측 안 함).

소스 분리: grounded 결과는 ``dart-ksic-v1`` 로 적재. 큐레이션 ``llm-cls-v1`` 은 보존(우선),
grounded는 미분류 갭만 채운다(스크리너 ``sector_map_multi`` 병합).
실행: ``python -m trading.sectors``.
"""

import os
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from trading.collectors.dart import DartClient
from trading.collectors.market import MarketStore
from trading.domains import Sector
from trading.screener import ScreenConfig, screen

GROUNDED_SOURCE = "dart-ksic-v1"


@dataclass(frozen=True)
class KsicRule:
    sectors: tuple[Sector, ...]
    confidence: float  # 채택 근거 순도(dominant/total)


_S = Sector
# KSIC 3자리 prefix → 규칙. 채택 기준: 실측 contingency에서 n>=3 & dominant>=0.75,
# 또는 n<3이라도 KSIC 의미가 명확하고 dominant와 일치(저n은 confidence 보수화).
# 주석 = [채택섹터 dominant/total · KSIC gloss]. 혼재(<0.75) 코드는 의도적으로 미수록.
KSIC_RULES: dict[str, KsicRule] = {
    "211": KsicRule((_S.PHARMA_BIO,), 1.00),       # 8/8 · 기초 의약물질
    "212": KsicRule((_S.PHARMA_BIO,), 1.00),       # 11/11 · 의약품 제조
    "271": KsicRule((_S.PHARMA_BIO,), 0.75),       # 3/4 · 의료용 기기
    "701": KsicRule((_S.PHARMA_BIO,), 1.00),       # 9/9 · 자연과학·공학 R&D(바이오)
    "261": KsicRule((_S.SEMICONDUCTOR,), 0.86),    # 12/14 · 반도체 제조
    "612": KsicRule((_S.TELECOM,), 1.00),          # 3/3 · 전기통신업
    "620": KsicRule((_S.AI_SOFTWARE,), 0.85),      # 2/2 · 컴퓨터 프로그래밍·SI
    "641": KsicRule((_S.FINANCIALS,), 1.00),       # 4/4 · 은행·금융
    "651": KsicRule((_S.FINANCIALS,), 1.00),       # 6/6 · 보험
    "661": KsicRule((_S.FINANCIALS,), 0.89),       # 8/9 · 금융지원 서비스
    "108": KsicRule((_S.FOOD_BEVERAGE,), 1.00),    # 4/4 · 기타 식품
    "471": KsicRule((_S.RETAIL_CONSUMER,), 1.00),  # 4/4 · 종합 소매
    "303": KsicRule((_S.AUTO,), 0.83),             # 5/6 · 자동차 부품
    "301": KsicRule((_S.AUTO,), 0.85),             # 2/2 · 자동차 완성차
    "283": KsicRule((_S.POWER_GRID,), 0.80),       # 4/5 · 절연선·전기장비
    "311": KsicRule((_S.SHIPBUILDING,), 0.75),     # 3/4 · 선박·보트
    "412": KsicRule((_S.CONSTRUCTION,), 0.75),     # 3/4 · 건물 건설
    "205": KsicRule((_S.CHEMICALS,), 0.85),        # 2/2 · 기타 화학제품
    # P-1 확장 버킷(2026-07-11 실측 — 게이트 통과 미분류 169종 DART induty 전수 + 개별 조회):
    "501": KsicRule((_S.SHIPPING_LOGISTICS,), 1.00),  # 3/3 · 해상 운송(HMM·팬오션·흥아해운)
    "529": KsicRule((_S.SHIPPING_LOGISTICS,), 0.75),  # 1/1 · 기타 운송관련 서비스(현대글로비스 물류, 저n 보수화)
    "511": KsicRule((_S.TRANSPORT,), 0.75),           # 1/1 · 항공 여객 운송(대한항공, 저n 보수화)
    "492": KsicRule((_S.TRANSPORT,), 0.75),           # 1/1 · 육상 여객 운송(동양고속, 저n 보수화)
    "91249": KsicRule((_S.LEISURE_CASINO,), 1.00),    # 3/3 · 사행시설(강원랜드·파라다이스·GKL)
    "752": KsicRule((_S.LEISURE_CASINO,), 0.75),      # 1/1 · 여행사(롯데관광개발, 저n 보수화)
    # P-1 실측의 부산물(기존 버킷 크로스워크 보강 — 같은 방법론):
    "20423": KsicRule((_S.COSMETICS,), 1.00),         # 5/5 · 화장품 제조(한국콜마·아모레퍼시픽·코스맥스·에이피알·달바글로벌)
}


MANUAL_SOURCE = "manual-curated-v1"
# KSIC가 못 잡는 유명주 큐레이션 오버라이드(LLM/전문가 판단 폴백). 환각가드: **확실한 것만**.
# 근거: 실측상 이들의 induty_code는 혼재 버킷(순도<0.75)이라 결정론 매핑 불가.
# 등록업종≠테마(다각화)이거나, 26 taxonomy에 정확 버킷이 있으나 KSIC 코드가 광범위한 경우.
# 모호·생소·taxonomy 버킷 부재(해운·운송·카지노 등)는 여기 넣지 않고 미분류 유지(→ PROPOSALS).
MANUAL_SECTORS: dict[str, tuple[str, tuple[Sector, ...]]] = {
    "011170": ("롯데케미칼", (_S.CHEMICALS,)),          # 20111 화학(bm/renewable 혼재 코드)
    "035900": ("JYP Ent.", (_S.ENTERTAINMENT,)),       # 59201 영상물제작(n부족)
    "328130": ("루닛", (_S.AI_SOFTWARE,)),             # 58222 응용SW(의료영상 AI)
    "054920": ("한컴위드", (_S.AI_SOFTWARE,)),          # 58221 시스템SW(보안)
    "174900": ("앱클론", (_S.PHARMA_BIO,)),            # 213 의약(cosmetics 혼재)
    "462350": ("이노스페이스", (_S.AEROSPACE_UAM,)),     # 31311 우주발사체(defense 혼재)
    "002020": ("코오롱", (_S.HOLDING,)),               # 64992 지주(financials 혼재)
    "003380": ("하림지주", (_S.HOLDING,)),              # 64992 지주
    "139130": ("iM금융지주", (_S.FINANCIALS, _S.HOLDING)),  # 64992 금융지주
    # 금융지주 일괄(64992 — iM금융지주와 동형, P-1 실측 2026-07-11에서 코드 확인):
    "105560": ("KB금융", (_S.FINANCIALS, _S.HOLDING)),
    "055550": ("신한지주", (_S.FINANCIALS, _S.HOLDING)),
    "086790": ("하나금융지주", (_S.FINANCIALS, _S.HOLDING)),
    "138040": ("메리츠금융지주", (_S.FINANCIALS, _S.HOLDING)),
    "316140": ("우리금융지주", (_S.FINANCIALS, _S.HOLDING)),
    "138930": ("BNK금융지주", (_S.FINANCIALS, _S.HOLDING)),
    "175330": ("JB금융지주", (_S.FINANCIALS, _S.HOLDING)),
    "088980": ("맥쿼리인프라", (_S.FINANCIALS,)),        # 64201 인프라투자펀드
    "103140": ("풍산", (_S.STEEL_MATERIALS, _S.DEFENSE)),  # 242 비철금속+방산(탄약)
    "323280": ("태성", (_S.SEMICONDUCTOR,)),           # 292 반도체·PCB 장비
    "051900": ("LG생활건강", (_S.COSMETICS,)),          # 20422 세제·비누(코드≠실체: 화장품 대형주, domains 대표종목)
}


def apply_manual_overrides(store: MarketStore, *, as_of: str, source: str = MANUAL_SOURCE) -> int:
    """큐레이션 오버라이드 적재(네트워크 불필요·멱등). 신규 적재 행 수 반환."""
    items = [
        {"srtn_cd": cd, "name": name, "sectors": [s.value for s in secs], "confidence": 0.99}
        for cd, (name, secs) in MANUAL_SECTORS.items()
    ]
    return store.upsert_sectors(items, source=source, as_of=as_of)


def _match(induty_code: str | None) -> KsicRule | None:
    """induty_code longest-prefix 매칭. 미수록 코드는 None(미분류)."""
    if not induty_code:
        return None
    code = induty_code.strip()
    for key in sorted(KSIC_RULES, key=len, reverse=True):
        if code.startswith(key):
            return KSIC_RULES[key]
    return None


def classify_ksic(induty_code: str | None) -> list[Sector]:
    """업종코드 → 섹터(다중 가능). 매핑 없으면 빈 리스트(추측 안 함)."""
    rule = _match(induty_code)
    return list(rule.sectors) if rule else []


@dataclass(frozen=True)
class ClassifySummary:
    attempted: int
    classified: int      # 신규 태깅된 종목수
    unclassified: int    # 매핑 못 해 미분류로 남긴 종목수
    by_sector: dict[str, int]


def classify_untagged(
    store: MarketStore,
    dart: DartClient,
    corp_map: dict[str, tuple[str, str]],
    codes: Sequence[tuple[str, str]],
    *,
    as_of: str,
    source: str = GROUNDED_SOURCE,
) -> ClassifySummary:
    """``codes``(srtn_cd, name) 각각 DART 회사개황 → KSIC 매핑 → ``stock_sectors`` 적재.

    매핑 실패·corp_code 없음은 'unclassified' 행으로 기록(추측 금지, 재시도 스킵용).
    """
    items: list[dict[str, object]] = []
    by_sector: Counter[str] = Counter()
    classified = unclassified = 0
    for srtn_cd, name in codes:
        ent = corp_map.get(srtn_cd)
        rule = _match(dart.company_profile(ent[0]).get("induty_code")) if ent else None
        if rule is None:
            items.append({"srtn_cd": srtn_cd, "name": name, "sectors": [], "confidence": 0.0})
            unclassified += 1
            continue
        secs = [s.value for s in rule.sectors]
        items.append({"srtn_cd": srtn_cd, "name": name, "sectors": secs, "confidence": rule.confidence})
        classified += 1
        for s in secs:
            by_sector[s] += 1
    store.upsert_sectors(items, source=source, as_of=as_of)
    return ClassifySummary(len(codes), classified, unclassified, dict(by_sector))


def main(argv: Sequence[str] | None = None) -> int:
    """기본: 미시도 종목만 grounded 분류. ``--retag``: KSIC 규칙이 늘어난 뒤
    과거에 '미분류'로 기록된 종목을 재평가(신규 규칙 소급 — 매칭 행만 append, 중복 무해)."""
    args = list(sys.argv[1:] if argv is None else argv)
    retag = "--retag" in args
    store = MarketStore()
    res = screen(store, ScreenConfig(top_n=1_000_000))  # 게이트 통과 전체 열거
    if not res.candidates:
        print("게이트 통과 종목 없음 — 분류 스킵")
        store.close()
        return 0
    n_manual = apply_manual_overrides(store, as_of=res.as_of)  # 큐레이션(네트워크 불필요)
    if n_manual:
        print(f"큐레이션 오버라이드(manual-curated-v1): 신규 {n_manual}행")

    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 미설정 — grounded 분류 스킵(blocked)")
        store.close()
        return 0
    tagged = set(store.sector_map_multi(("llm-cls-v1", MANUAL_SOURCE)))
    attempted = store.codes_with_any_row(GROUNDED_SOURCE)
    if retag:
        grounded_ok = set(store.sector_map(GROUNDED_SOURCE))  # 이미 실분류된 종목은 제외
        skip = tagged | grounded_ok
    else:
        skip = tagged | attempted
    todo = [(c.srtn_cd, c.name) for c in res.candidates if c.srtn_cd not in skip]
    if not todo:
        print(f"grounded 보강 불필요 — 게이트 {res.universe}종목 모두 태깅/시도됨")
        store.close()
        return 0
    dart = DartClient(key)
    corp_map = dart.corp_code_map()
    summary = classify_untagged(store, dart, corp_map, todo, as_of=res.as_of)
    store.close()
    print(
        f"섹터 보강(dart-ksic-v1) as_of={res.as_of}: 대상 {summary.attempted} · "
        f"신규분류 {summary.classified} · 미분류유지 {summary.unclassified}"
    )
    for sec, n in sorted(summary.by_sector.items(), key=lambda x: -x[1]):
        print(f"  {sec}: {n}")
    return 0


__all__ = [
    "GROUNDED_SOURCE",
    "MANUAL_SOURCE",
    "MANUAL_SECTORS",
    "KSIC_RULES",
    "KsicRule",
    "ClassifySummary",
    "apply_manual_overrides",
    "classify_ksic",
    "classify_untagged",
]


if __name__ == "__main__":
    raise SystemExit(main())
