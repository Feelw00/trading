"""R4 촉매 적대검증 — 선별·perspective-diverse·다수결(LLM 주입, 프로세스 없음)."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from trading.contracts.event import AffectedStock, EventRecord, Verification
from trading.llm import LLMError
from trading.rounds.r4 import R4Config, run_r4, select_events, verify_event, verify_lens

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 9, 16, 0, tzinfo=KST)


def _evt(eid: str = "evt.1", *, scope: str = "single_stock", strength: float | None = 0.8,
         verification: Verification | None = None, **over: Any) -> EventRecord:
    base: dict[str, Any] = {
        "id": eid, "as_of": NOW, "fetched_at": NOW, "source": "r2:test", "type": "corp_action",
        "summary_1line": "요약", "scope": scope, "catalyst_strength": strength,
        "catalyst_type": "supply_chain",
        "affected": [AffectedStock(srtn_cd="001740", relevance=0.9)],
        "evidence": ["n1"], "verification": verification,
    }
    base.update(over)
    return EventRecord(**base)


class _SeqClient:
    """렌즈 호출 순서(strength→linkage→timing)대로 응답을 순환 반환."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.i = 0

    def complete(self, prompt: str) -> str:
        r = self.responses[self.i % len(self.responses)]
        self.i += 1
        return r


class _BoomClient:
    def complete(self, prompt: str) -> str:
        raise LLMError("boom")


def test_select_high_single_or_veryhigh_only() -> None:
    evs = [
        _evt("a", scope="single_stock", strength=0.8),                  # 선별
        _evt("b", scope="single_stock", strength=0.3),                  # single 저강도 → 제외
        _evt("c", scope="broad_market", strength=0.75),                 # 초고강도(≥0.7) → 선별
        _evt("d", scope="broad_market", strength=0.5),                  # broad 저강도 → 제외
        _evt("e", scope="single_stock", strength=0.9,
             verification=Verification(verified_by="r4", confirmed=True)),  # 이미 검증 → 제외
    ]
    sel = select_events(evs, R4Config())
    assert [e.id for e in sel] == ["a", "c"]  # 강도 내림차순(0.8, 0.75)


def test_select_skips_none_strength() -> None:
    assert select_events([_evt("a", strength=None)], R4Config()) == []


def test_verify_lens_parses_survived() -> None:
    v = verify_lens(_SeqClient(['{"survived": true, "reason": "근거 충분"}']), _evt(), "strength", [])
    assert v.survived is True and v.lens == "strength" and v.reason == "근거 충분"


def test_verify_lens_llm_error_is_skeptical() -> None:
    v = verify_lens(_BoomClient(), _evt(), "timing", [])
    assert v.survived is False and "실패" in v.reason  # 회의적 기본


def test_verify_event_confirmed_on_majority_survival() -> None:
    c = _SeqClient([
        '{"survived": true, "reason": "a"}',
        '{"survived": true, "reason": "b"}',
        '{"survived": false, "reason": "c"}',
    ])
    ver = verify_event(c, _evt(), [], R4Config())
    assert ver.confirmed is True and len(ver.lens_verdicts) == 3


def test_verify_event_refuted_on_majority_refute() -> None:
    c = _SeqClient([
        '{"survived": false, "reason": "a"}',
        '{"survived": false, "reason": "b"}',
        '{"survived": true, "reason": "c"}',
    ])
    ver = verify_event(c, _evt(), [], R4Config())
    assert ver.confirmed is False


def test_run_r4_attaches_verification_and_preserves_fields() -> None:
    c = _SeqClient(['{"survived": true, "reason": "유효"}'])  # 순환 → 3렌즈 모두 생존
    res = run_r4(c, [_evt("a", strength=0.85)], {}, config=R4Config())
    assert res.selected == 1 and len(res.verified) == 1 and res.confirmed == 1
    ev = res.verified[0]
    assert ev.verification is not None and ev.verification.confirmed
    assert ev.id == "a" and ev.catalyst_strength == 0.85  # model_copy 원 필드 보존
    assert ev.verification.verified_by == "r4:claude"


def test_run_r4_skips_when_nothing_selected() -> None:
    res = run_r4(_BoomClient(), [_evt("a", scope="broad_market", strength=0.4)], {}, config=R4Config())
    assert res.selected == 0 and res.verified == []


def test_config_from_env_overrides() -> None:
    cfg = R4Config.from_env(
        {
            "R4_STRENGTH_THRESHOLD": "0.4",
            "R4_HIGH_STRENGTH": "0.6",
            "R4_MIN_SURVIVED": "3",
            "R4_MAX_EVENTS": "10",
        }
    )
    assert cfg == R4Config(
        strength_threshold=0.4, high_strength=0.6, min_survived=3, max_events=10
    )


def test_config_from_env_defaults_when_unset() -> None:
    assert R4Config.from_env({}) == R4Config()
    assert R4Config.from_env({"R4_STRENGTH_THRESHOLD": ""}) == R4Config()
