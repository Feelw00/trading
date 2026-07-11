"""R3 페르소나 분석 — 입력격리·invalidation 재생성·ThesisRecord 구성(LLM 주입)."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from trading.contracts.event import AffectedStock, EventRecord, EventType, Scope
from trading.contracts.thesis import Persona
from trading.domains import CatalystType
from trading.rounds.r3 import events_for_persona, run_r3

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 9, 16, 0, tzinfo=KST)
CAND = ("001740", "SK네트웍스")

_VALID = (
    '{"direction":"long","thesis":"수주 모멘텀","instrument_class":"SK네트웍스",'
    '"trigger":"전고 돌파","invalidation":"종가 5만원 하회","horizon_days":7,'
    '"confidence":0.5,"evidence":["evt.x"]}'
)
_NO_INVAL = (
    '{"direction":"long","thesis":"논제","instrument_class":"SK네트웍스",'
    '"trigger":"t","horizon_days":7,"confidence":0.5,"evidence":[]}'
)


class _Always:
    def __init__(self, resp: str) -> None:
        self.resp = resp
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return self.resp


class _StrictAware:
    """비-strict는 invalidation 없는 응답, strict([재생성])는 valid → 재생성 회복 검증."""

    def complete(self, prompt: str) -> str:
        return _VALID if "[재생성]" in prompt else _NO_INVAL


def _evt(eid: str, ctype: CatalystType | None) -> EventRecord:
    return EventRecord(
        id=eid, as_of=NOW, fetched_at=NOW, source="r2:test", type=EventType.CORP_ACTION,
        summary_1line="요약", catalyst_type=ctype, scope=Scope.SINGLE_STOCK, catalyst_strength=0.6,
        affected=[AffectedStock(srtn_cd="001740", relevance=0.8)],
    )


def test_events_for_persona_isolation() -> None:
    evs = [
        _evt("a", CatalystType.FLOW_DEMAND),
        _evt("b", CatalystType.EARNINGS),
        _evt("c", CatalystType.MACRO),
        _evt("d", None),
    ]
    assert {e.id for e in events_for_persona(Persona.SUPPLY, evs)} == {"a", "d"}   # flow_demand + None
    assert {e.id for e in events_for_persona(Persona.CYCLE, evs)} == {"b", "d"}    # earnings + None
    assert {e.id for e in events_for_persona(Persona.MACRO, evs)} == {"c", "d"}    # macro + None


def test_run_r3_three_personas() -> None:
    res = run_r3(_Always(_VALID), CAND, None, [], now=NOW)
    assert len(res.theses) == 3 and res.rejected == 0
    personas = {t.persona for t in res.theses}
    assert personas == {Persona.SUPPLY, Persona.CYCLE, Persona.MACRO}
    supply = next(t for t in res.theses if t.persona == Persona.SUPPLY)
    assert supply.id == "thesis.20260609.001740.supply"
    assert supply.invalidation == "종가 5만원 하회" and supply.horizon_days == 7


def test_run_r3_rejects_when_invalidation_missing() -> None:
    client = _Always(_NO_INVAL)
    res = run_r3(client, CAND, None, [], now=NOW)
    assert res.theses == [] and res.rejected == 3       # 3 페르소나 전부 폐기
    assert client.calls == 6                            # 페르소나당 2회(원본+재생성)


def test_run_r3_retry_recovers() -> None:
    res = run_r3(_StrictAware(), CAND, None, [], now=NOW)
    assert len(res.theses) == 3 and res.rejected == 0   # 재생성(strict)에서 invalidation 회복


def test_run_r3_clamps_horizon_and_confidence() -> None:
    bad = (
        '{"direction":"sideways","thesis":"t","instrument_class":"x","trigger":"t",'
        '"invalidation":"조건","horizon_days":999,"confidence":5}'
    )
    res = run_r3(_Always(bad), CAND, None, [], now=NOW)
    t = res.theses[0]
    assert t.horizon_days == 7              # 범위밖 → 기본
    assert t.confidence == 0.3              # 범위밖 → 기본
    assert t.direction.value == "flat"     # 미지 direction → flat


# ── P-9 3단계 스윙 승격 ────────────────────────────────────────────────


def test_build_prompt_injects_swing_extra_lines() -> None:
    from trading.rounds.r3 import PERSONAS, build_prompt

    note = "스윙 승격 근거(P-9, as_of 20260610): 기회 트리거 pullback 발화 · 스윙 품질 점수 0.85"
    for spec in PERSONAS:  # 전 페르소나 공통 주입
        p = build_prompt(spec, CAND, None, [], [], extra_lines=(note,))
        assert note in p
        p_without = build_prompt(spec, CAND, None, [], [])
        assert "스윙 승격" not in p_without  # 비승격 종목은 기존 프롬프트 불변


def test_run_r3_passes_extra_lines_to_all_personas() -> None:
    class _Capture:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return _VALID

    cap = _Capture()
    run_r3(cap, CAND, None, [], extra_lines=("스윙 승격 근거: 테스트",), now=NOW)
    assert len(cap.prompts) == 3 and all("스윙 승격 근거: 테스트" in p for p in cap.prompts)


def test_swing_promotions_merges_and_caps(tmp_path: Any) -> None:
    from trading.reason_news import _swing_promotions
    from trading.swing import AxisValue, SwingResult, SwingRow, SwingStore

    def _row(cd: str, name: str, score: float, trigs: tuple[str, ...]) -> SwingRow:
        return SwingRow(
            cd, name, "KOSPI", 100.0, (), trend=AxisValue(1.0, True), domain=AxisValue(),
            fund=AxisValue(), flow=AxisValue(), mdd=-0.1, score=score, pct={}, triggers=trigs,
        )

    rows = [
        _row("111110", "고점수", 0.9, ("pullback", "catalyst")),
        _row("222220", "중간", 0.7, ("domain_ignition",)),
        _row("333330", "저점수", 0.6, ("flow_turn",)),
    ]
    res = SwingResult("20260610", rows, 10, 3, {}, {}, (), rows)
    store = SwingStore()  # conftest가 기본 경로 격리
    store.record(res)
    store.close()

    promos = _swing_promotions(limit=2)  # 점수순 상한 → 저점수 탈락
    assert set(promos) == {"111110", "222220"}
    assert "pullback" in promos["111110"] and "catalyst" in promos["111110"]  # 트리거 병합
    assert "0.90" in promos["111110"] and "as_of 20260610" in promos["111110"]


def test_swing_promotions_empty_without_snapshot() -> None:
    from trading.reason_news import _swing_promotions

    assert _swing_promotions(limit=5) == {}  # 스냅샷 없음 — 승격 없음(발명 금지)
