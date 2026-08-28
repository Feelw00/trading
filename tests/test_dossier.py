"""R4.5 심사 패킷 — bear 의무·환각 가드 카드·멱등·저장 테스트 (LLM은 가짜 클라이언트)."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading.contracts.longterm import (
    CandidateRecord,
    CyclePhase,
    CycleRecord,
    DossierRecord,
    PrimaryAxes,
    ValuationRecord,
)
from trading.dossier import DossierStore, build_fact_card, write_dossier

KST = ZoneInfo("Asia/Seoul")
TS = datetime(2026, 8, 28, 11, 0, tzinfo=KST)


def _cand() -> CandidateRecord:
    return CandidateRecord(
        id="cand.20260828.051910", as_of=TS, fetched_at=TS, source="derived:test",
        symbol="051910", industry="화학·정유", sector_krx="화학", phase=CyclePhase.RECOVERING,
        passed=True, industry_pbr_pct=0.07,
        unapplied=["환원·거버넌스 가점(PIVOT-3 수집 전)"],
        valuation_ref="val.x", cycle_ref="cyc.x",
    )


def _val() -> ValuationRecord:
    return ValuationRecord(
        id="val.20260827.051910", as_of=TS, fetched_at=TS, source="derived:test",
        symbol="051910", sector_krx="화학", pbr=0.85, per=12.3, roe=0.07,
        debt_ratio=0.6, loss_years_5y=0, loss_years_observed=5, sector_pbr_pct=0.08,
        fin_basis="BS 2026/11012 · IS 2025/11011",
    )


def _cyc() -> CycleRecord:
    return CycleRecord(
        id="cyc.20260828.화학", as_of=TS, fetched_at=TS, source="derived:test",
        industry="화학", phase=CyclePhase.RECOVERING, temperature=30,
        axes_primary=PrimaryAxes(
            sector_pbr_band_pct=0.36, sector_margin_band_pct=0.25, sector_rev_cycle_z=0.05
        ),
        secular_decline=False,
    )


class _FakeClient:
    model = "fake-model"

    def __init__(self, payload: dict[str, list[str]]) -> None:
        self._payload = payload
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self._payload, ensure_ascii=False)


def test_bear_case_is_mandatory_schema() -> None:
    with pytest.raises(ValidationError):
        DossierRecord(
            id="d.x", as_of=TS, fetched_at=TS, source="llm:test",
            candidate_ref="cand.x", symbol="051910", industry="화학·정유",
            model="m", bull_case=["좋다"], bear_case=[], fact_card="카드",
        )


def test_fact_card_honest_gaps_and_values() -> None:
    card = build_fact_card(_cand(), _val(), _cyc())
    assert "PBR 0.85" in card and "recovering" in card and "하위 7%" in card
    assert "자료 없음" in card          # 환원 미수집 — 결측 정직
    assert "미포함" in card             # 수급 창 축적 전


def test_write_dossier_injects_card_and_enforces_bear(tmp_path: Path) -> None:
    fake = _FakeClient({"bull_case": ["저평가"], "bear_case": ["사이클 판단 오류 가능", "환원 자료 없음"], "risks": ["유가"]})
    d = write_dossier(fake, "fake-model", _cand(), _val(), _cyc())
    assert "PBR 0.85" in fake.prompts[0]           # 카드가 프롬프트에 주입됨
    assert d.model == "fake-model" and len(d.bear_case) == 2
    assert d.candidate_ref == "cand.20260828.051910"

    # LLM이 bear를 비우면 스키마가 반려한다(§3 의무)
    lazy = _FakeClient({"bull_case": ["좋다"], "bear_case": [], "risks": []})
    with pytest.raises(ValidationError):
        write_dossier(lazy, "fake-model", _cand(), _val(), _cyc())


def test_store_idempotency(tmp_path: Path) -> None:
    store = DossierStore(tmp_path / "d.sqlite")
    fake = _FakeClient({"bull_case": ["a"], "bear_case": ["b", "c"], "risks": []})
    d = write_dossier(fake, "fake-model", _cand(), _val(), _cyc())
    assert store.append(d) == 1
    assert store.exists_for_candidate("cand.20260828.051910")
    assert not store.exists_for_candidate("cand.없음")
    store.close()
