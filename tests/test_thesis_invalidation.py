"""AC: invalidation(무효화 조건) 없는 ThesisRecord 생성 시도가 실패함을 증명."""

from typing import Any

import pytest
from pydantic import ValidationError

from trading.contracts.thesis import ThesisRecord


def test_valid_thesis_has_invalidation(thesis_kwargs: dict[str, Any]) -> None:
    assert ThesisRecord(**thesis_kwargs).invalidation


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_invalidation_rejected(thesis_kwargs: dict[str, Any], bad: str) -> None:
    thesis_kwargs["invalidation"] = bad
    with pytest.raises(ValidationError):
        ThesisRecord(**thesis_kwargs)


def test_missing_invalidation_rejected(thesis_kwargs: dict[str, Any]) -> None:
    del thesis_kwargs["invalidation"]
    with pytest.raises(ValidationError):
        ThesisRecord(**thesis_kwargs)
