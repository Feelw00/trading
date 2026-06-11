"""DiscussPack — 종목 토론(`/discuss`)의 사전 조립 컨텍스트 (PROPOSALS P-5).

FactPack(가격·공시·재무·뉴스·수급 5일)을 품고, 토론에 추가로 필요한 것을 얹는다:
단기 변동률, 투자자별 누적 포지션(연기금 분리), 뉴스 사실검증 결과(R2→R4 박제).
**결정론 조립** — LLM 개입은 뉴스 검증(기존 R2/R4 경로)뿐. 결측은 notes(추측 금지).
캐시는 append-only 버전 레코드(journal/discuss.py).
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading.contracts.base import NonEmptyStr
from trading.contracts.factpack import FactPack


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FlowCumulative(_Frozen):
    """최근 N거래일 투자자별 누적 순매수(백만원). 연기금(기금) 분리.

    days_counted: 실제 합산된 거래일 수(데이터 부족 시 days 미만 — 부분합 명시).
    비수치 행은 건너뛴다(추측 금지). 기관(연기금外) = 기관계 − 기금.
    """

    days: int                            # 목표 윈도우(5/20)
    days_counted: int                    # 실제 합산 일수
    prsn_mn: float | None                # 개인
    frgn_mn: float | None                # 외국인
    fund_mn: float | None                # 기금(연기금)
    orgn_ex_fund_mn: float | None        # 기관(연기금外)


class EventBrief(_Frozen):
    """토론용 이벤트 요약 1건 — EventStore 박제분의 슬라이스(원본은 id로 역참조).

    **status 해석 주의(운영자 2026-06-11):** R4 적대검증은 기본 회의적 설계라 기각이
    과대 산출된다. ``refuted`` 는 "촉매 가치에 대한 적대 의견(다수결)"이지 **사실 부정이
    아니다** — 근거 위계는 공시(1차 사료) > 가격·수급(관측) > R4 의견 > 뉴스. 그래서
    렌즈 생존 수·사유를 함께 박아 이진 라벨만으로 판단하지 않게 한다.
    """

    id: NonEmptyStr
    summary_1line: NonEmptyStr
    catalyst_type: str | None = None
    scope: str | None = None             # single_stock | sector_theme | broad_market
    strength: float | None = None
    status: NonEmptyStr                  # confirmed | refuted | unverified
    verified_by: str | None = None       # r4:claude(촉매 가치) | r4:fact-check(사실성) — 의미 구분
    lenses: str | None = None            # 렌즈 생존비 "1/3" (unverified는 None)
    lens_notes: list[str] = Field(default_factory=list)  # 렌즈별 한줄 사유(생존여부 표기)
    as_of: AwareDatetime


class DiscussPack(_Frozen):
    """토론 컨텍스트 팩 — 같은 종목 재토론 시 캐시 재사용(갱신은 운영자 확인)."""

    fact: FactPack                       # 가격맥락·공시·재무·뉴스·수급(일별 5건)·섹터
    price_chg_5d_pct: float | None = None    # 최근 5거래일 변동률(%)
    flows_cum: list[FlowCumulative] = Field(default_factory=list)   # 5/20일 누적
    events: list[EventBrief] = Field(default_factory=list)          # 종목 + 소속 섹터 이벤트
    built_at: AwareDatetime              # 조립 시각(KST)
    notes: list[str] = Field(default_factory=list)   # 팩 수준 결측·보강 이력


__all__ = ["DiscussPack", "EventBrief", "FlowCumulative"]
