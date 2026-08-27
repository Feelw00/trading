"""정책 파라미터 v1.0 — 2026-08-27 운영자 결재분 (docs/POLICY_PARAMS.md가 원본 문서).

결재 내역: ① 조선=종목 큐레이션 ② 화이트리스트↔KRX 버킷 매핑 승인 ③ 실물 보강 축
metric 키·월 1회 수동 입력 승인 ④ 검증 사이클=운송·창고 2024(1회, 재사용 금지).
개정은 R7 루프 + 운영자 결재로만(헌장 2). R4 스크리너 임계 등은 미결(§5) — 여기 없음.
"""

POLICY_VERSION = "policy-v1.0 (2026-08-27)"

# 사이클 화이트리스트(②) — R4 편입 대상 산업 → R3 밴드 그룹 키.
# R3는 전 섹터를 계측하지만 편입은 이 목록만(스코프 규율).
WHITELIST: dict[str, str] = {
    "메모리반도체": "전기·전자",
    "화학·정유": "화학",
    "철강": "금속",
    "해운·물류": "운송·창고",
    "조선": "조선(큐레이션)",     # ①(b) — 버킷 혼합(자동차·방산) 회피, 아래 큐레이션 그룹
    "건설기계": "기계·장비",
    "은행": "금융",
    "증권": "증권",
}

# ①(b) 조선 큐레이션 그룹 — 게이트 유니버스 실측(2026-08-27, 운송장비·부품 버킷 10종목 중
# 조선 3사). 유니버스 밖 조선사(HD한국조선해양 등) 추가는 운영자 결재 사항.
CURATED_GROUPS: dict[str, list[str]] = {
    "조선(큐레이션)": [
        "329180",  # HD현대중공업
        "010140",  # 삼성중공업
        "042660",  # 한화오션
    ],
}

# ③ 실물 보강 축 metric 키(수동 입력 채널 — 게이트 아님, 가점·조기 신호)
AUX_METRIC_KEYS: dict[str, list[str]] = {
    "해운·물류": ["shipping.bdi", "shipping.scfi"],
    "메모리반도체": ["semis.dram_fixed"],
    "화학·정유": ["chem.spread", "oil.crack_margin"],
    "철강": ["steel.spread"],
    "조선": ["ship.newbuild_index", "ship.orderbook"],
    "은행": ["rates.spread_3y10y"],
    "증권": ["rates.spread_3y10y"],
}

# ④ 검증 사이클 — 임계 고정 후 1회만 사용, 실패로 임계를 고치면 재사용 금지(PIVOT-7 ⑥)
VALIDATION_CYCLE: tuple[str, str] = ("운송·창고", "2024")

__all__ = ["AUX_METRIC_KEYS", "CURATED_GROUPS", "POLICY_VERSION", "VALIDATION_CYCLE", "WHITELIST"]
