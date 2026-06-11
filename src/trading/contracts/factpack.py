"""FactPack — R3 페르소나 분석의 **입력 슬라이스**(설계서 §6 "외부 데이터 추론 금지").

후보 1종목당 grounded 사실 묶음: 가격맥락(DB) + 공시(DART) + 재무(DART).
**결정론 조립** — LLM 미개입. 없는 데이터는 빈 값 + ``notes``에 사유, 절대 지어내지 않는다.
각 구성요소는 출처(source)·as_of를 보존(환각가드).
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading.contracts.base import NonEmptyStr
from trading.contracts.news import NewsItem


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PriceContext(_Frozen):
    """DB(전종목 EOD)에서 결정론 산출. 스크리너 신호와 동일 정의."""

    as_of: NonEmptyStr          # bas_dt(거래일)
    market: str | None
    close: float
    market_cap: float | None    # mrkt_tot_amt
    tr_value_surge: float       # 당일 거래대금 / 20일 평균
    mom_short_pct: float        # 20일 수익률(%)
    mom_long_pct: float         # 60일 수익률(%)
    high_252_proximity: float   # 종가 / 252일 최고가


class FlowLine(_Frozen):
    """투자자별 순매수 1거래일(KIS 투자자매매동향 — 단위: 백만원, 실호출 관측 확정)."""

    bas_dt: NonEmptyStr             # 거래일 YYYYMMDD
    prsn_ntby_mn: float | None      # 개인 순매수 대금(백만원)
    frgn_ntby_mn: float | None      # 외국인 순매수 대금(백만원)
    orgn_ntby_mn: float | None      # 기관계 순매수 대금(백만원) — 기금 포함 합산(실관측 검증)
    fund_ntby_mn: float | None = None  # 기금(연기금) 순매수 대금(백만원, KIS 공식 라벨 "기금")


class DisclosureItem(_Frozen):
    """DART 공시 목록 1건(원본 필드 보존)."""

    rcept_dt: NonEmptyStr       # 접수일 YYYYMMDD
    report_nm: NonEmptyStr      # 보고서명
    rcept_no: NonEmptyStr       # 접수번호(원문 링크 키)
    flr_nm: str | None = None   # 공시 제출인


class FinancialLine(_Frozen):
    """재무 주요계정 1줄(당기/전기 + YoY는 결정론 산술)."""

    account: NonEmptyStr        # 정규화 라벨(매출액·영업이익·당기순이익·자산총계 등)
    fs_div: str | None          # CFS(연결)/OFS(별도)
    thstrm: float | None        # 당기 금액
    frmtrm: float | None        # 전기 금액
    yoy_pct: float | None       # (당기-전기)/|전기| × 100


class FactPack(_Frozen):
    srtn_cd: NonEmptyStr
    name: NonEmptyStr
    sectors: list[str] = Field(default_factory=list)
    screen_score: float
    price: PriceContext
    disclosures: list[DisclosureItem] = Field(default_factory=list)
    fin_period: str | None = None        # 사용한 재무 기간 "bsns_year/reprt_code"
    financials: list[FinancialLine] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)     # 최근 grounded 뉴스(있으면)
    flows: list[FlowLine] = Field(default_factory=list)    # 최근 수급(KIS, 최신순 — 있으면)
    sources: dict[str, str] = Field(default_factory=dict)  # 구성요소별 출처
    notes: list[str] = Field(default_factory=list)         # 결측·미수집 사유(추측 대체 금지)
    as_of: AwareDatetime         # 조립 기준 거래일 시각
    fetched_at: AwareDatetime    # 조립 시각(KST)


__all__ = ["DisclosureItem", "FactPack", "FinancialLine", "FlowLine", "PriceContext"]
