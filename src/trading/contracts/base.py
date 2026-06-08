"""공통 레코드 베이스 — 모든 데이터 계약의 부모.

설계서 §4 / CLAUDE.md:
- 모든 레코드는 as_of/fetched_at/source 필수
- 모든 타임스탬프는 timezone-aware(naive datetime 금지)
- 레코드는 불변(frozen) — 수정은 새 버전 레코드 append로만(저널 계층)
"""

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints

# 공백 strip 후 최소 1자 — 빈/공백 문자열을 검증 단계에서 거부
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class BaseRecord(BaseModel):
    """모든 계약 레코드의 공통 베이스.

    - ``extra="forbid"``: 스키마 외 필드 거부(추측 필드 유입 차단)
    - ``frozen=True``: 불변 — 수정은 새 버전 레코드 append로만
    - ``as_of``/``fetched_at``: :class:`AwareDatetime` → naive datetime 거부
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    as_of: AwareDatetime
    fetched_at: AwareDatetime
    source: NonEmptyStr


__all__ = ["BaseRecord", "NonEmptyStr"]
