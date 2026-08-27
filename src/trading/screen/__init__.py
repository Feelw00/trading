"""R4 편입·제외 스크리너 — 순수 코드(설계서 v0.3 §3 R4). 편입은 규칙만이 결정한다."""

from trading.screen.rules import PROPOSED_R4, ScreenParams, evaluate
from trading.screen.run import run_screen
from trading.screen.store import CandidateStore

__all__ = ["CandidateStore", "PROPOSED_R4", "ScreenParams", "evaluate", "run_screen"]
