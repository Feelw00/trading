"""도메인 taxonomy — 수집/정형화/분석 라운드를 관통하는 3축(지역·자산클래스·섹터).

OPEN_QUESTIONS(도메인 분리). FactRecord 등 계약이 이 축으로 분류된다.
- Region: 국내/국외
- AssetClass: index / fx / macro / news / sector
- Sector: 26개(아래 4그룹). 종목은 다중 소속 가능 → FactRecord.sector 는 리스트.

수집 대상은 SectorMeta.active 로 제어(Phase-1: 반도체만 on). taxonomy 정의 != 수집 구현.
대표 종목(kr_examples)은 placeholder — 실제 구성종목 리스트는 별도 확정.
"""

from dataclasses import dataclass
from enum import Enum


class Region(str, Enum):
    KR = "KR"  # 국내
    US = "US"  # 국외(미국 중심)


class AssetClass(str, Enum):
    INDEX = "index"    # 지수 (KOSPI·KOSDAQ / NASDAQ·S&P·SOX)
    FX = "fx"          # 환율 (USD/KRW·NDF)
    MACRO = "macro"    # 금리·유가 등 매크로
    NEWS = "news"      # 경제 뉴스 텍스트(정형화 대상)
    SECTOR = "sector"  # 섹터 종목 수치


class Sector(str, Enum):
    # 성장/테크
    SEMICONDUCTOR = "semiconductor"
    DISPLAY = "display"
    AI_SOFTWARE = "ai_software"
    INTERNET_GAME = "internet_game"
    ROBOTICS = "robotics"
    BATTERY_CELL = "battery_cell"
    BATTERY_MATERIALS = "battery_materials"
    # 산업/중후장대
    DEFENSE = "defense"
    SHIPBUILDING = "shipbuilding"
    AUTO = "auto"
    POWER_GRID = "power_grid"
    NUCLEAR = "nuclear"
    RENEWABLE = "renewable"
    AEROSPACE_UAM = "aerospace_uam"
    STEEL_MATERIALS = "steel_materials"
    CHEMICALS = "chemicals"
    MACHINERY = "machinery"
    CONSTRUCTION = "construction"
    # 바이오/소비
    PHARMA_BIO = "pharma_bio"
    COSMETICS = "cosmetics"
    ENTERTAINMENT = "entertainment"
    FOOD_BEVERAGE = "food_beverage"
    RETAIL_CONSUMER = "retail_consumer"
    # 금융/방어
    FINANCIALS = "financials"
    TELECOM = "telecom"
    HOLDING = "holding"


GROUP_TECH = "성장/테크"
GROUP_INDUSTRIAL = "산업/중후장대"
GROUP_BIO_CONSUMER = "바이오/소비"
GROUP_FIN_DEFENSIVE = "금융/방어"


@dataclass(frozen=True)
class SectorMeta:
    sector: Sector
    label_ko: str
    group: str
    active: bool  # 수집 대상 여부 (Phase-1: 반도체만 on)
    kr_examples: tuple[str, ...]  # 대표 종목 placeholder(구성종목 확정은 별도)


def _meta(
    sector: Sector, label_ko: str, group: str, examples: tuple[str, ...], *, active: bool = False
) -> SectorMeta:
    return SectorMeta(sector=sector, label_ko=label_ko, group=group, active=active, kr_examples=examples)


SECTORS: dict[Sector, SectorMeta] = {
    # 성장/테크
    Sector.SEMICONDUCTOR: _meta(Sector.SEMICONDUCTOR, "반도체", GROUP_TECH, ("삼성전자", "SK하이닉스"), active=True),
    Sector.DISPLAY: _meta(Sector.DISPLAY, "디스플레이", GROUP_TECH, ("LG디스플레이", "덕산네오룩스")),
    Sector.AI_SOFTWARE: _meta(Sector.AI_SOFTWARE, "AI·SW/플랫폼", GROUP_TECH, ("네이버", "카카오")),
    Sector.INTERNET_GAME: _meta(Sector.INTERNET_GAME, "인터넷·게임", GROUP_TECH, ("크래프톤", "넷마블")),
    Sector.ROBOTICS: _meta(Sector.ROBOTICS, "로봇", GROUP_TECH, ("레인보우로보틱스", "두산로보틱스")),
    Sector.BATTERY_CELL: _meta(Sector.BATTERY_CELL, "2차전지(셀)", GROUP_TECH, ("LG에너지솔루션", "삼성SDI")),
    Sector.BATTERY_MATERIALS: _meta(Sector.BATTERY_MATERIALS, "전지소재", GROUP_TECH, ("에코프로비엠", "포스코퓨처엠")),
    # 산업/중후장대
    Sector.DEFENSE: _meta(Sector.DEFENSE, "방산", GROUP_INDUSTRIAL, ("한화에어로스페이스", "LIG넥스원")),
    Sector.SHIPBUILDING: _meta(Sector.SHIPBUILDING, "조선", GROUP_INDUSTRIAL, ("HD현대중공업", "한화오션")),
    Sector.AUTO: _meta(Sector.AUTO, "자동차·부품", GROUP_INDUSTRIAL, ("현대차", "기아")),
    Sector.POWER_GRID: _meta(Sector.POWER_GRID, "전력기기·그리드", GROUP_INDUSTRIAL, ("HD현대일렉트릭", "LS일렉트릭")),
    Sector.NUCLEAR: _meta(Sector.NUCLEAR, "원자력", GROUP_INDUSTRIAL, ("두산에너빌리티",)),
    Sector.RENEWABLE: _meta(Sector.RENEWABLE, "신재생(풍력·태양광)", GROUP_INDUSTRIAL, ("씨에스윈드",)),
    Sector.AEROSPACE_UAM: _meta(Sector.AEROSPACE_UAM, "우주항공·UAM", GROUP_INDUSTRIAL, ("한화시스템",)),
    Sector.STEEL_MATERIALS: _meta(Sector.STEEL_MATERIALS, "철강·소재", GROUP_INDUSTRIAL, ("POSCO홀딩스",)),
    Sector.CHEMICALS: _meta(Sector.CHEMICALS, "화학", GROUP_INDUSTRIAL, ("LG화학", "롯데케미칼")),
    Sector.MACHINERY: _meta(Sector.MACHINERY, "기계·산업재", GROUP_INDUSTRIAL, ("두산밥캣",)),
    Sector.CONSTRUCTION: _meta(Sector.CONSTRUCTION, "건설", GROUP_INDUSTRIAL, ("현대건설",)),
    # 바이오/소비
    Sector.PHARMA_BIO: _meta(Sector.PHARMA_BIO, "제약·바이오", GROUP_BIO_CONSUMER, ("삼성바이오로직스", "셀트리온")),
    Sector.COSMETICS: _meta(Sector.COSMETICS, "화장품", GROUP_BIO_CONSUMER, ("아모레퍼시픽", "LG생활건강")),
    Sector.ENTERTAINMENT: _meta(Sector.ENTERTAINMENT, "엔터·미디어", GROUP_BIO_CONSUMER, ("하이브", "JYP Ent.")),
    Sector.FOOD_BEVERAGE: _meta(Sector.FOOD_BEVERAGE, "음식료", GROUP_BIO_CONSUMER, ("CJ제일제당",)),
    Sector.RETAIL_CONSUMER: _meta(Sector.RETAIL_CONSUMER, "유통·소비재", GROUP_BIO_CONSUMER, ("이마트",)),
    # 금융/방어
    Sector.FINANCIALS: _meta(Sector.FINANCIALS, "금융(은행·증권·보험)", GROUP_FIN_DEFENSIVE, ("KB금융", "삼성화재")),
    Sector.TELECOM: _meta(Sector.TELECOM, "통신", GROUP_FIN_DEFENSIVE, ("SKT", "KT")),
    Sector.HOLDING: _meta(Sector.HOLDING, "지주", GROUP_FIN_DEFENSIVE, ("삼성물산",)),
}


def active_sectors() -> list[Sector]:
    """현재 수집 대상으로 켜진 섹터."""
    return [s for s, meta in SECTORS.items() if meta.active]


class CatalystType(str, Enum):
    """촉매유형축 — 뉴스 사건의 **성격** 분류(섹터축 `Sector`와 직교, PROPOSALS P-4 §2).

    R7 캘리브레이션이 "어떤 촉매유형 가설이 적중하나"를 학습하려면 이 라벨이 필요하다.
    `EventType`(사건의 구조형)과 별개 축 — 같은 사건이 두 축에서 동시에 분류된다.
    """

    EARNINGS = "earnings"                  # 실적(확정 발표)
    GUIDANCE = "guidance"                  # 가이던스·전망 변경
    POLICY_REGULATION = "policy_regulation"  # 정책·규제
    MA_RESTRUCTURE = "ma_restructure"      # M&A·구조조정
    SUPPLY_CHAIN = "supply_chain"          # 공급망(수주·증설·차질)
    FLOW_DEMAND = "flow_demand"            # 수급(투자자별·자금흐름)
    MACRO = "macro"                        # 거시(금리·환율·유가·지정학)
    PRODUCT_TECH = "product_tech"          # 제품·기술(신제품·기술이정표)
    LEGAL = "legal"                        # 소송·법적분쟁
    MANAGEMENT = "management"              # 경영진·지배구조
    RUMOR_UNCONFIRMED = "rumor_unconfirmed"  # 미확인 소문(환각가드: UNVERIFIED)


__all__ = [
    "AssetClass",
    "CatalystType",
    "Region",
    "Sector",
    "SectorMeta",
    "SECTORS",
    "active_sectors",
    "GROUP_TECH",
    "GROUP_INDUSTRIAL",
    "GROUP_BIO_CONSUMER",
    "GROUP_FIN_DEFENSIVE",
]
