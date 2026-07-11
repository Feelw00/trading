"""P-2 LLM 폴백 분류기 — 프롬프트·검증·임계·환각가드 테스트 (프로세스 없이 fake client)."""

import json
from pathlib import Path

from trading.collectors.market import MarketStore
from trading.domains import SECTORS
from trading.llm import LLMError
from trading.sector_llm import (
    FALLBACK_SOURCE,
    SectorLLMConfig,
    build_prompt,
    classify_unclassified,
    client_for_sectors,
)

_BATCH = [("111110", "가상반도체", "KOSDAQ"), ("222220", "가상물산", "KOSPI"), ("333330", "모르는회사", None)]


class _FakeLLM:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self._payload, Exception):
            raise self._payload
        return json.dumps(self._payload, ensure_ascii=False)


def test_build_prompt_lists_taxonomy_and_stocks() -> None:
    p = build_prompt(_BATCH)
    for s, meta in SECTORS.items():
        assert f"{s.value}: {meta.label_ko}" in p  # 29 taxonomy 동적 생성(하드코딩 금지)
    assert "111110 가상반도체 (KOSDAQ)" in p
    assert "333330 모르는회사" in p
    assert "추측하지 마라" in p  # 환각가드 문구


def test_classify_adopts_confident_and_keeps_unknown_unclassified(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    fake = _FakeLLM([
        {"srtn_cd": "111110", "sectors": ["semiconductor"], "confidence": 0.9, "basis": "반도체 장비"},
        {"srtn_cd": "222220", "sectors": ["holding"], "confidence": 0.4, "basis": "지주"},  # 저신뢰 → 미채택
        {"srtn_cd": "333330", "sectors": [], "confidence": 0.0, "basis": ""},  # 모름 → 미분류
    ])
    out = classify_unclassified(store, fake, _BATCH)
    assert out.attempted == 3 and out.classified == 1 and out.unclassified == 2
    assert out.rejected == 0
    sm = store.sector_map(FALLBACK_SOURCE)
    assert sm == {"111110": ["semiconductor"]}
    # 미채택도 시도 기록은 남아 재실행 시 스킵
    assert store.codes_with_any_row(FALLBACK_SOURCE) == {"111110", "222220", "333330"}
    store.close()


def test_classify_rejects_invented_sector_and_foreign_code(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    fake = _FakeLLM([
        {"srtn_cd": "111110", "sectors": ["quantum_computing"], "confidence": 0.9, "basis": "x"},  # 발명 값
        {"srtn_cd": "999999", "sectors": ["semiconductor"], "confidence": 0.9, "basis": "x"},  # 배치 밖
        {"srtn_cd": "222220", "sectors": ["retail_consumer"], "confidence": 0.9, "basis": "유통"},
    ])
    out = classify_unclassified(store, fake, _BATCH)
    assert out.rejected == 2
    assert out.classified == 1  # 222220만 채택
    assert store.sector_map(FALLBACK_SOURCE) == {"222220": ["retail_consumer"]}
    store.close()


def test_classify_requires_basis(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    fake = _FakeLLM([
        {"srtn_cd": "111110", "sectors": ["semiconductor"], "confidence": 0.95, "basis": ""},
    ])
    out = classify_unclassified(store, fake, _BATCH)
    assert out.classified == 0  # 근거 없는 고신뢰 → 미채택(환각가드)
    store.close()


def test_classify_caps_multi_sector(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    fake = _FakeLLM([
        {"srtn_cd": "111110", "sectors": ["semiconductor", "display", "robotics"],
         "confidence": 0.9, "basis": "다각화"},
    ])
    out = classify_unclassified(store, fake, _BATCH, config=SectorLLMConfig(max_sectors=2))
    assert out.classified == 1
    assert set(store.sector_map(FALLBACK_SOURCE)["111110"]) == {"semiconductor", "display"}
    store.close()


def test_batch_error_skips_without_attempt_record(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    fake = _FakeLLM(LLMError("타임아웃"))
    out = classify_unclassified(store, fake, _BATCH)
    assert out.attempted == 0 and len(out.batch_errors) == 1
    assert store.codes_with_any_row(FALLBACK_SOURCE) == set()  # 재시도 가능하게 기록 없음
    store.close()


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    fake = _FakeLLM([
        {"srtn_cd": "111110", "sectors": ["semiconductor"], "confidence": 0.9, "basis": "장비"},
    ])
    out = classify_unclassified(store, fake, _BATCH, dry_run=True)
    assert out.classified == 1
    assert store.codes_with_any_row(FALLBACK_SOURCE) == set()
    store.close()


def test_client_for_sectors_model_precedence() -> None:
    c1 = client_for_sectors({"SECTOR_LLM_MODEL": "m-sector", "R2_MODEL": "m-r2", "CLAUDE_MODEL": "m-base"})
    c2 = client_for_sectors({"R2_MODEL": "m-r2", "CLAUDE_MODEL": "m-base"})
    c3 = client_for_sectors({})
    assert getattr(c1, "model", None) == "m-sector"
    assert getattr(c2, "model", None) == "m-r2"
    assert getattr(c3, "model", None) is None  # claude 기본(하드코딩 없음)
