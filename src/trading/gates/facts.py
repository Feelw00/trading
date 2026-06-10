"""R1 — FactRecord 신선도·정합성 게이트 (순수 코드, LLM 금지).

설계서 §3 R1 / CLAUDE.md 절대금지 #2. 뉴스 게이트(``gates.news``)와 같은 패턴:
플래그는 ``(records, now, config)`` 의 **결정론 함수** → 영속화하지 않고 매 조회 시 재계산.
부착이 필요하면 :func:`apply_flags` 로 새 버전 레코드를 만들어 append(저널 규약).

- ``stale``: ``as_of`` 가 신선도 허용치(소스·메트릭별 knob)보다 오래됨.
  **폐기하지 않는다** — 단 R5(주문 초안)는 :func:`require_decision_grade` 하드 게이트로 차단.
- ``future_dated``: ``as_of`` 가 미래(시계·파싱 오류) — stale 판정 회피를 막는다.
- ``conflict``: 핵심 지표(환율·지수)의 **이중 소스 임계 괴리**. 평균·임의 선택하지 않고
  그룹 전체를 플래그 + 알림 훅 — 사람이 해소할 때까지 :func:`split_decision_inputs` 가
  의사결정에서 제외한다. (실패 박제: 캐시 선물 지표 오독, 8,851억 vs 53조 수급 오보.)

landing(SQLite 원시행) → FactRecord 변환은 이 게이트의 소관이 아니다(collectors.base 참조).
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from trading.collectors.base import KST, now_kst
from trading.contracts.fact import FactFlag, FactRecord
from trading.domains import AssetClass
from trading.journal.store import AlertHook, log_alert_hook

# conflict 감시 대상 — 설계서 §3 R1 "핵심 지표(환율, 지수)"
_CONFLICT_CLASSES = frozenset({AssetClass.FX, AssetClass.INDEX})


class GateError(RuntimeError):
    """의사결정 등급 미달 입력이 하드 게이트에 걸렸다(R5 주문 초안 차단)."""


@dataclass(frozen=True)
class FactGateConfig:
    """게이트 임계(전부 knob). 기본값은 보수적 — 운영 결정으로 조정.

    신선도 허용치 해석 순서: ``per_source`` > ``per_metric`` > ``default_max_age_hours``.
    기본 96h = 일별 EOD(+1영업일 공개) + 주말 갭 흡수. 유가(FRED)는 구조적 공표 지연(~9일
    관측)이 있어 운영 시 ``per_metric`` 으로 완화해 주입한다(기본값에 박지 않음 — 명시 knob).
    """

    default_max_age_hours: float = 96.0
    per_metric: Mapping[str, float] = field(default_factory=dict)   # metric → 허용 시간(h)
    per_source: Mapping[str, float] = field(default_factory=dict)   # source → 허용 시간(h)
    future_skew_minutes: float = 60.0          # 미래 as_of 허용 오차(시계 skew)
    conflict_threshold_pct: float = 0.5        # 이중 소스 상대 괴리 임계(%)

    @classmethod
    def from_file(cls, path: str | Path) -> "FactGateConfig":
        """JSON 설정 파일 주입(M2 §2: 소스별 신선도 허용치 설정 파일).

        키: ``default_max_age_hours`` / ``per_metric`` / ``per_source`` /
        ``future_skew_minutes`` / ``conflict_threshold_pct`` (전부 선택).
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            default_max_age_hours=float(raw.get("default_max_age_hours", 96.0)),
            per_metric={str(k): float(v) for k, v in raw.get("per_metric", {}).items()},
            per_source={str(k): float(v) for k, v in raw.get("per_source", {}).items()},
            future_skew_minutes=float(raw.get("future_skew_minutes", 60.0)),
            conflict_threshold_pct=float(raw.get("conflict_threshold_pct", 0.5)),
        )

    def max_age(self, record: FactRecord) -> timedelta:
        hours = self.per_source.get(
            record.source, self.per_metric.get(record.metric, self.default_max_age_hours)
        )
        return timedelta(hours=hours)


@dataclass(frozen=True)
class FactVerdict:
    record: FactRecord
    flags: frozenset[FactFlag]

    @property
    def fresh(self) -> bool:
        """신선도 결함(stale/future_dated) 없음."""
        return not (self.flags & {FactFlag.STALE, FactFlag.FUTURE_DATED})

    @property
    def decision_usable(self) -> bool:
        """플래그 전무 — 의사결정(R3~R5) 입력 가능. conflict는 사람 해소까지 제외."""
        return not self.flags


@dataclass(frozen=True)
class DecisionSplit:
    usable: tuple[FactRecord, ...]
    excluded: tuple[FactVerdict, ...]


def _freshness_flags(
    record: FactRecord, now: datetime, config: FactGateConfig
) -> set[FactFlag]:
    flags: set[FactFlag] = set()
    if record.as_of > now + timedelta(minutes=config.future_skew_minutes):
        flags.add(FactFlag.FUTURE_DATED)
    elif record.as_of < now - config.max_age(record):
        flags.add(FactFlag.STALE)
    return flags


def _conflict_ids(
    records: Sequence[FactRecord], config: FactGateConfig, alert_hook: AlertHook
) -> set[str]:
    """핵심 지표(환율·지수)의 동일 metric·동일 as_of(KST 일자) 이중 소스 괴리 감지.

    metric은 계약상 구체 시리즈 식별자(설계서 §4 예: ``kospi_foreign_net_buy_krw``)여야
    같은 관측치끼리만 비교된다. 괴리 시 그룹 **전체** 를 플래그 — 평균·임의 선택 금지.
    """
    groups: dict[tuple[str, str], list[FactRecord]] = {}
    for r in records:
        if r.asset_class not in _CONFLICT_CLASSES:
            continue
        key = (r.metric, r.as_of.astimezone(KST).date().isoformat())
        groups.setdefault(key, []).append(r)

    conflicted: set[str] = set()
    for (metric, day), group in groups.items():
        sources = {r.source for r in group}
        if len(sources) < 2:
            continue
        values = [r.value for r in group]
        lo, hi = min(values), max(values)
        denom = max(abs(lo), abs(hi))
        if denom == 0:
            continue
        deviation_pct = (hi - lo) / denom * 100.0
        if deviation_pct > config.conflict_threshold_pct:
            conflicted.update(r.id for r in group)
            alert_hook(
                f"dual-source conflict on {metric}",
                severity="P1",
                context={
                    "metric": metric,
                    "as_of_date": day,
                    "sources": sorted(sources),
                    "values": values,
                    "deviation_pct": round(deviation_pct, 4),
                },
            )
    return conflicted


def gate_facts(
    records: Sequence[FactRecord],
    *,
    now: datetime | None = None,
    config: FactGateConfig | None = None,
    alert_hook: AlertHook = log_alert_hook,
) -> list[FactVerdict]:
    """배치 검증 — 신선도(레코드별) + 정합성(이중 소스 교차) 플래그 산출(결정론)."""
    resolved_now = now if now is not None else now_kst()
    cfg = config if config is not None else FactGateConfig()
    conflicted = _conflict_ids(records, cfg, alert_hook)
    verdicts: list[FactVerdict] = []
    for r in records:
        flags = _freshness_flags(r, resolved_now, cfg)
        if r.id in conflicted:
            flags.add(FactFlag.CONFLICT)
        verdicts.append(FactVerdict(record=r, flags=frozenset(flags)))
    return verdicts


def apply_flags(verdict: FactVerdict) -> FactRecord:
    """플래그를 부착한 **새 버전 레코드** 생성(원본 불변 — append-only 저널 규약)."""
    merged = sorted({*verdict.record.flags, *verdict.flags}, key=lambda f: f.value)
    return verdict.record.model_copy(update={"flags": merged})


def split_decision_inputs(verdicts: Sequence[FactVerdict]) -> DecisionSplit:
    """의사결정 입력 분리 — 플래그 보유분은 **제외 마킹**(평균·임의 선택 없음)."""
    usable = tuple(v.record for v in verdicts if v.decision_usable)
    excluded = tuple(v for v in verdicts if not v.decision_usable)
    return DecisionSplit(usable=usable, excluded=excluded)


def require_decision_grade(verdicts: Sequence[FactVerdict]) -> list[FactRecord]:
    """R5 입력 하드 게이트 — stale/future/conflict 가 섞이면 주문 초안 생성 불가.

    설계서 §3 R1: "R5는 stale 입력으로 주문 초안 생성 불가 — 하드 게이트".
    R5는 입력 집합 확정 후 이 함수를 통과시켜야 하며, 결함 입력은 예외로 차단된다
    (조용한 드롭 금지 — 무엇이 왜 막혔는지 박제).
    """
    bad = [(v.record.id, sorted(f.value for f in v.flags)) for v in verdicts if v.flags]
    if bad:
        detail = ", ".join(f"{rid}{flags}" for rid, flags in bad)
        raise GateError(f"decision-grade violation: {detail}")
    return [v.record for v in verdicts]


def summarize(verdicts: Sequence[FactVerdict]) -> dict[str, int]:
    """플래그 분포 집계(보고용)."""
    counts: dict[str, int] = {"total": len(verdicts), "decision_usable": 0, "fresh": 0}
    for f in FactFlag:
        counts[f.value] = 0
    for v in verdicts:
        counts["decision_usable"] += int(v.decision_usable)
        counts["fresh"] += int(v.fresh)
        for f in v.flags:
            counts[f.value] += 1
    return counts


__all__ = [
    "DecisionSplit",
    "FactGateConfig",
    "FactVerdict",
    "GateError",
    "apply_flags",
    "gate_facts",
    "require_decision_grade",
    "split_decision_inputs",
    "summarize",
]
