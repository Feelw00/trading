"""용어 사전 — 웹 전 페이지의 지표 설명 단일 소스(P-16 V1). 설명은 정책 결재값과 동기."""

import html

from trading.contracts.longterm import CyclePhase, phase_ko

GLOSSARY: dict[str, tuple[str, str]] = {
    # 밸류에이션
    "pbr": ("PBR", "주가순자산비율 — 시가총액 ÷ 자본총계. 1.0 미만이면 장부가치보다 싸게 거래되는 상태"),
    "per": ("PER", "주가수익비율 — 시가총액 ÷ 최근 연간 순이익. 연간 흑자일 때만 산출(— 표시는 적자·미산출)"),
    "psr": ("PSR", "주가매출비율 — 시가총액 ÷ 최근 연간 매출"),
    "roe": ("ROE", "자기자본이익률 — 최근 연간 순이익 ÷ 자본총계. 0 이하면 R4에서 가치 함정 방어로 탈락"),
    "roe_median": ("ROE 5y중앙", "최근 5년 ROE의 중앙값 — 사이클을 관통한 수익성. 3% 미만은 만성 저수익으로 탈락"),
    "debt": ("부채비율", "부채총계 ÷ 자본총계. 2.0(200%) 초과 탈락 — 은행·증권은 면제(레버리지가 사업 모델)"),
    "sector_pct": ("산업내 PBR", "같은 산업 안에서 PBR이 하위 몇 %인가 — 낮을수록 산업 대비 저평가. 하위 40% 이내만 통과"),
    "loss5y": ("적자 5y", "최근 5년 중 적자였던 연수 ÷ 관측 가능한 연수. 예: 1/5 = 5년 중 1년 적자. 2년 이상이면 탈락"),
    "r4": ("R4 판정", "규칙 기반 편입 심사 결과 — 발동 존(바닥·회복 산업) → 가치 → 생존력·수익성 순으로 거른다. 통과 = 관찰 후보(매수 결정 아님)"),
    # 산업·사이클
    "band_pct": ("PBR밴드", "산업 합산 PBR이 자기 역사(2020~) 대비 하위 몇 %인가 — 30% 이하 바닥 존, 75% 이상 과열 존"),
    "margin_band": ("마진밴드", "산업 영업이익률이 자기 역사 대비 하위 몇 %인가(은행·증권은 ROE 밴드로 대체)"),
    "rev_z": ("매출z", "최근 연간 매출 증감률이 과거 평균 대비 몇 표준편차인가 — 0보다 크면 평균 이상 성장 국면"),
    "temp": ("온도", "PBR밴드와 마진밴드의 평균 ×100 — 0에 가까울수록 역사적 저평가, 100에 가까울수록 과열"),
    "improving": ("개선", "마진 또는 매출 증감률이 직전 연도보다 좋아졌는가 — 바닥 통과 판정의 필수 조건(위치≠반전)"),
    "secular": ("사양", "산업 매출의 장기(8년) 추세가 하향인가 — 하향이면 아무리 싸도 편입하지 않음(구조적 쇠퇴)"),
    "amplitude": ("사이클 진폭", "산업 PBR 밴드의 역사적 최고 ÷ 최저 — 사이클의 크기. 4배면 바닥에서 고점까지 4배 재평가된 역사"),
    "phase": ("국면", "바닥 통과=하단+개선 시작(진입 존) · 회복=중간+개선(진입 존) · 과열=상단 · 하강=개선 없음 · 판정 불가=관측 부족"),
}

PHASE_CLASS = {
    CyclePhase.BOTTOMING: "ph-bott",
    CyclePhase.RECOVERING: "ph-reco",
    CyclePhase.OVERHEATED: "ph-over",
    CyclePhase.DECLINING: "ph-decl",
    CyclePhase.UNKNOWN: "ph-unkn",
}


def tip(key: str, label: str | None = None) -> str:
    """호버 설명이 붙는 용어 스팬 — 헤더·라벨용."""
    name, desc = GLOSSARY[key]
    return (
        f"<span class='tip' data-tip='{html.escape(desc, quote=True)}'>"
        f"{html.escape(label or name)}</span>"
    )


def phase_pill(phase: CyclePhase) -> str:
    """국면 색 배지 — 전 페이지 공통 시각 언어(P-16 V1)."""
    return f"<span class='ph {PHASE_CLASS[phase]}'>{phase_ko(phase)}</span>"


__all__ = ["GLOSSARY", "PHASE_CLASS", "phase_pill", "tip"]
