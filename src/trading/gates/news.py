"""R1 — 뉴스 신선도·정합성 게이트 (순수 코드, LLM 금지).

설계서 §3 R1 / CLAUDE.md 절대금지 #2: 게이트 판단에 LLM을 넣지 않는다.
R0가 적재한 ``NewsItem`` 에 **플래그를 부착**한다(폐기하지 않음 — 설계서 "폐기하지 않되").
다운스트림 규약:
- R2(분류·스코어): stale·저신뢰는 가중을 낮춘다(컨텍스트로는 사용).
- R5(주문 초안): **신선도 결함(stale/undated/future)은 하드 게이트** — 신선하지 않은 입력으로 주문 초안 불가.

플래그는 ``(item, now, config)`` 의 **결정론 함수** → **영속화하지 않는다**(now에 상대적이라 저장하면
플래그 자체가 stale해진다). 매 조회 시 재계산.

설계서 R1의 이중-소스 ``conflict``(환율·지수 임계 괴리 + 알림)는 **FactRecord 인스턴스의 별도 게이트** —
뉴스엔 이중-소스 수치 비교가 없어 비적용. 뉴스 '정합성'은 **출처 무결성**(undated/future_dated/low_trust)으로 구현.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from trading.collectors.base import now_kst
from trading.contracts.news import NewsItem


class NewsFlag(str, Enum):
    STALE = "stale"                # published_at 이 신선도 지평보다 오래됨
    UNDATED = "undated"            # published_at 미상 — 신선도 판정 불가
    FUTURE_DATED = "future_dated"  # published_at 이 미래(시계·파싱 오류 — 실패 박제)
    LOW_TRUST = "low_trust"        # trust < 임계 (COLLECT-4 UNVERIFIED)


# R5 하드게이트(주문 초안 불가)를 거는 신선도 결함들. low_trust는 여기 안 듦(가중 낮춤 대상).
_FRESHNESS_FLAGS = frozenset({NewsFlag.STALE, NewsFlag.UNDATED, NewsFlag.FUTURE_DATED})


@dataclass(frozen=True)
class GateConfig:
    """게이트 임계(전부 knob). 기본값은 보수적 — 운영 결정으로 조정."""

    max_age_days: float = 3.0          # 촉매 신선도 지평(설계서 swing 3~15d, R5 하드게이트 기준)
    min_trust: float = 0.5             # 이 미만이면 LOW_TRUST(현 데이터 trust 0.5 바닥 → 기본은 unknown 통과)
    future_skew_minutes: float = 60.0  # 미래 발행 허용 오차(시계 skew)


@dataclass(frozen=True)
class NewsVerdict:
    item: NewsItem
    flags: frozenset[NewsFlag]

    @property
    def usable(self) -> bool:
        """플래그 전무 = 신선·정합. (폐기 아님 — 다운스트림이 가중/하드게이트로 처리)."""
        return not self.flags

    @property
    def fresh(self) -> bool:
        """R5 하드게이트 기준 — 신선도 결함(stale/undated/future) 없음(low_trust는 허용)."""
        return not (self.flags & _FRESHNESS_FLAGS)


def gate_item(item: NewsItem, now: datetime, config: GateConfig) -> NewsVerdict:
    """단일 기사 검증 — now·config 대비 신선도·정합성 플래그 산출(결정론)."""
    flags: set[NewsFlag] = set()
    pub = item.published_at
    if pub is None:
        flags.add(NewsFlag.UNDATED)
    elif pub > now + timedelta(minutes=config.future_skew_minutes):
        flags.add(NewsFlag.FUTURE_DATED)
    elif pub < now - timedelta(days=config.max_age_days):
        flags.add(NewsFlag.STALE)
    if item.trust < config.min_trust:
        flags.add(NewsFlag.LOW_TRUST)
    return NewsVerdict(item=item, flags=frozenset(flags))


def gate_news(
    items: Sequence[NewsItem],
    *,
    now: datetime | None = None,
    config: GateConfig | None = None,
) -> list[NewsVerdict]:
    """배치 검증. now 미지정 시 KST 현재(tz-aware). config 미지정 시 기본 임계."""
    resolved_now = now if now is not None else now_kst()
    cfg = config if config is not None else GateConfig()
    return [gate_item(it, resolved_now, cfg) for it in items]


def summarize(verdicts: Sequence[NewsVerdict]) -> dict[str, int]:
    """플래그 분포 집계(보고용). total·usable·fresh + 플래그별 카운트."""
    counts: dict[str, int] = {"total": len(verdicts), "usable": 0, "fresh": 0}
    for f in NewsFlag:
        counts[f.value] = 0
    for v in verdicts:
        counts["usable"] += int(v.usable)
        counts["fresh"] += int(v.fresh)
        for f in v.flags:
            counts[f.value] += 1
    return counts


def main() -> int:
    """ops 가시성 — 최근 적재 뉴스에 R1 게이트 적용 후 플래그 분포 출력(영속화 없음)."""
    from trading.collectors.news import NewsStore

    store = NewsStore()
    items = store.recent(limit=500)
    store.close()
    s = summarize(gate_news(items))
    print(f"R1 뉴스 게이트: total={s['total']} usable={s['usable']} fresh={s['fresh']}")
    print(
        f"  flags: stale={s['stale']} undated={s['undated']} "
        f"future_dated={s['future_dated']} low_trust={s['low_trust']}"
    )
    return 0


__all__ = [
    "GateConfig",
    "NewsFlag",
    "NewsVerdict",
    "gate_item",
    "gate_news",
    "summarize",
]


if __name__ == "__main__":
    raise SystemExit(main())
