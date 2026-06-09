"""NewsItem — 뉴스 수집 landing 계약(출처 가드 내장, COLLECT-4).

웹서치 허용(뉴스 한정)이라도 환각 방지는 **출처 강제**로: URL·발행처·published_at(KST)·source 필수,
사실은 fetch된 기사에만 귀속. 날짜 미상·검증불가는 ``verified=False``(드롭 후 날조 금지).
정규화·dedup·trust는 ``collectors.news`` 의 결정론 코드가 부여(LLM 미개입).
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading.contracts.base import NonEmptyStr


class NewsItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr                       # dedup 키(정규화 URL 해시)
    source: NonEmptyStr                   # 백엔드: naver | searxng
    query: NonEmptyStr                    # 이 기사를 끌어온 쿼리
    title: NonEmptyStr
    url: NonEmptyStr
    publisher: str | None = None
    published_at: AwareDatetime | None = None  # 발행 시각(KST) — 미상 가능
    fetched_at: AwareDatetime             # 수집 시각(KST, 항상)
    snippet: str | None = None
    lang: str | None = None               # ko | en | ...
    entities: list[str] = Field(default_factory=list)  # 후보 srtn_cd / 테마 슬러그
    trust: float = Field(ge=0.0, le=1.0, default=0.0)  # 발행처·소스 신뢰
    verified: bool = False                # published_at 존재 + URL 유효


__all__ = ["NewsItem"]
