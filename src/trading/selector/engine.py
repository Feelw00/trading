"""R5.5 — 아침 플레이북 선택기 엔진 (08:50, 결정론적, **LLM 금지**. 설계서 §3 R5.5).

08~10시 경로에 새로운 판단은 없다 — 분석은 전날 20:30(R5)에 끝났고 아침은
**패턴 매칭과 집행뿐**이다. 이 엔진은 순수 함수: (플레이북, 흐름 관측 스냅샷) → 활성화 결정.

- 가장 흔한 출력은 "오늘 해당 없음, **비거래**"여야 정상(갭 전략의 엣지는 분포가 쏠린 날만).
- 조건 평가는 AND — arm_conditions 전부 충족해야 활성.
- **관측치 누락·평가 불가 조건 = 미충족**(보수 기본값). 값을 추측하지 않는다(환각 가드).
- 조건식 문법은 ``<op><숫자>`` (op: <, <=, >, >=, ==) — R5가 산출하는 흐름 변수 조건의
  계약. 비숫자 조건(시각 등)은 평가 불가 → 비활성 + 사유 박제.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from trading.contracts.playbook import Playbook, PlaybookState

# 종목별 흐름 관측치: {srtn_cd: {flow_var: value}} — 소스 어댑터(NXT 등)는 OPEN_QUESTIONS SEL-1
FlowSnapshot = Mapping[str, Mapping[str, float]]

_COND = re.compile(r"^\s*(<=|>=|==|<|>)\s*(-?\d+(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class ConditionEval:
    var: str
    expr: str
    observed: float | None
    met: bool
    note: str = ""


@dataclass(frozen=True)
class Activation:
    playbook: Playbook
    state: PlaybookState
    evals: tuple[ConditionEval, ...] = field(default_factory=tuple)

    @property
    def active(self) -> bool:
        return self.state is PlaybookState.ACTIVE


@dataclass(frozen=True)
class SelectionResult:
    activations: tuple[Activation, ...]

    @property
    def active(self) -> tuple[Activation, ...]:
        return tuple(a for a in self.activations if a.active)

    @property
    def no_trade(self) -> bool:
        """비거래 — 활성 플레이북 없음(기본·정상 경로)."""
        return not self.active


def eval_condition(var: str, expr: str, observed: float | None) -> ConditionEval:
    """조건 1개 평가 — 관측치 없음/문법 불일치는 미충족(추측 금지)."""
    if observed is None:
        return ConditionEval(var, expr, None, met=False, note="관측치 없음")
    m = _COND.match(expr)
    if m is None:
        return ConditionEval(var, expr, observed, met=False, note=f"평가 불가 조건식: {expr!r}")
    op, raw = m.group(1), float(m.group(2))
    met = {
        "<": observed < raw,
        "<=": observed <= raw,
        ">": observed > raw,
        ">=": observed >= raw,
        "==": observed == raw,
    }[op]
    return ConditionEval(var, expr, observed, met=met)


def _srtn_of(playbook: Playbook) -> str:
    # id 규약 pb.<YYYYMMDD>.<srtn>.<side> (rounds/r5)
    parts = playbook.id.split(".")
    return parts[2] if len(parts) > 3 else ""


def evaluate_playbook(playbook: Playbook, snapshot: FlowSnapshot) -> Activation:
    """플레이북 1개 — arm_conditions 전부(AND) 충족 시에만 ACTIVE."""
    obs = snapshot.get(_srtn_of(playbook), {})
    evals = tuple(
        eval_condition(var, expr, obs.get(var))
        for var, expr in playbook.arm_conditions.items()
    )
    state = PlaybookState.ACTIVE if evals and all(e.met for e in evals) else PlaybookState.INACTIVE
    return Activation(playbook=playbook, state=state, evals=evals)


def select(playbooks: list[Playbook], snapshot: FlowSnapshot) -> SelectionResult:
    """PlaybookSet 전체 평가 — 입력이 비면 빈 결과(비거래)."""
    return SelectionResult(
        activations=tuple(evaluate_playbook(pb, snapshot) for pb in playbooks)
    )


__all__ = [
    "Activation",
    "ConditionEval",
    "FlowSnapshot",
    "SelectionResult",
    "eval_condition",
    "evaluate_playbook",
    "select",
]
