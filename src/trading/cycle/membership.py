"""토스 테마 스냅샷 기반 큐레이션 그룹 파생 — PIVOT-10 멤버십 매핑 (policy-v1.5).

원천: 운영자 수동 익스포트(토스 앱 산업 테마, 분기 1회 갱신 리듬)를 증류한 패키지 스냅샷
`data/toss_themes_YYYY-MM-DD.json`. 원본 42파일은 `data/toss_themes/<날짜>/`(git 제외).

- 셀렉터 = (테마, 카테고리) 튜플. 카테고리 "*"는 테마 전체.
- 다중 소속 자연 허용(운영자 원칙 2026-08-31): 한 종목이 여러 그룹 셀렉터에 걸리면 전부 편입.
  밴드는 그룹별 합산·R4는 산업별 심사라 구조적으로 정합.
- 오버라이드(add/remove)는 policy.py에서 선언 — 기존 운영자 확정(v1.3 지주 판단 등)이
  토스 태깅보다 우선하는 지점의 명시적 보정 경로.
"""

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.resources import files

Selector = tuple[str, str]  # (테마, 카테고리) — 카테고리 "*" = 테마 전체

SNAPSHOT_NAME = "toss_themes_2026-08-31.json"


@lru_cache(maxsize=1)
def _snapshot() -> tuple[dict[str, str], ...]:
    raw = files("trading.cycle").joinpath("data").joinpath(SNAPSHOT_NAME).read_text("utf-8")
    payload = json.loads(raw)
    rows: list[dict[str, str]] = payload["rows"]
    return tuple(rows)


def snapshot_as_of() -> str:
    raw = files("trading.cycle").joinpath("data").joinpath(SNAPSHOT_NAME).read_text("utf-8")
    as_of: str = json.loads(raw)["as_of"]
    return as_of


def snapshot_names() -> dict[str, str]:
    """스냅샷의 {종목코드: 종목명} — 수집 대상 표시용."""
    return {row["code"]: row["name"] for row in _snapshot()}


def build_curated_groups(
    selectors: Mapping[str, Sequence[Selector]],
    *,
    add: Mapping[str, Sequence[str]] | None = None,
    remove: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    """셀렉터·오버라이드로 {그룹명: [종목코드]}를 결정론적(코드 정렬)으로 파생."""
    out: dict[str, list[str]] = {}
    for group, sels in selectors.items():
        codes: set[str] = set()
        for theme, category in sels:
            codes |= {
                row["code"]
                for row in _snapshot()
                if row["theme"] == theme and (category == "*" or row["category"] == category)
            }
        codes |= set((add or {}).get(group, ()))
        codes -= set((remove or {}).get(group, ()))
        out[group] = sorted(codes)
    return out


__all__ = [
    "SNAPSHOT_NAME",
    "Selector",
    "build_curated_groups",
    "snapshot_as_of",
    "snapshot_names",
]
