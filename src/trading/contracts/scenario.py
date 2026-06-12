"""ScenarioAxis — R5 시나리오 합성의 구조화 단위(축 → 분기/리스크 줄).

배경(2026-06-12 운영자 피드백): R5가 시나리오를 자유 산문 한 덩어리로 내면
저녁 보고가 통문단이 된다 — 사후 정규식 재구성은 마커 변형에 깨진다(실측).
구조는 산출 시점(R5 JSON 스키마)에 강제하고, 저장·렌더는 이 계약만 오간다.

저장 포맷은 JSON 배열(synth_runs.scenario_tree TEXT 컬럼 재사용).
구조화 이전에 적재된 산문 레코드는 ``axes_from_stored`` 가 줄 단위 축 하나로
감싸 하위 호환한다(내용 무변경).
"""

import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from trading.contracts.base import NonEmptyStr


class ScenarioAxis(BaseModel):
    """시나리오 한 축 — 제목 + 줄 단위 분기/조건/리스크(1줄 1항목)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = ""                                    # 레거시 산문 래핑은 무제목 허용
    lines: list[NonEmptyStr] = Field(default_factory=list)


def axes_to_stored(axes: Sequence[ScenarioAxis]) -> str:
    """ScenarioAxis 목록 → 저장 텍스트(JSON 배열)."""
    return json.dumps([a.model_dump() for a in axes], ensure_ascii=False)


def axes_from_stored(text: str) -> list[ScenarioAxis]:
    """저장 텍스트 → ScenarioAxis 목록. JSON 배열이 아니면 레거시 산문으로 간주."""
    stripped = text.strip()
    if not stripped:
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        out: list[ScenarioAxis] = []
        for item in data:
            if isinstance(item, dict):
                try:
                    out.append(ScenarioAxis.model_validate(item))
                except ValueError:
                    continue
        return out
    # 레거시 산문 — 줄 단위로 보존(내용 무변경, 무제목 축 하나)
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    return [ScenarioAxis(title="", lines=lines)] if lines else []


__all__ = ["ScenarioAxis", "axes_from_stored", "axes_to_stored"]
