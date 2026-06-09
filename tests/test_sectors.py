"""grounded 섹터 분류(DART 업종 → KSIC 크로스워크) + 회사개황 파싱 + 스크리너 병합."""

from pathlib import Path
from typing import Any

import pytest

from trading.collectors.base import CollectError
from trading.collectors.dart import DartClient
from trading.collectors.market import MarketStore
from trading.domains import Sector
from trading.sectors import GROUNDED_SOURCE, classify_ksic, classify_untagged


def test_classify_ksic_high_purity_codes() -> None:
    assert classify_ksic("212") == [Sector.PHARMA_BIO]      # 대원제약
    assert classify_ksic("620") == [Sector.AI_SOFTWARE]     # 크레오에스지
    assert classify_ksic("261") == [Sector.SEMICONDUCTOR]


def test_classify_ksic_uses_three_digit_prefix() -> None:
    # 5자리 입력도 3자리 규칙으로 매칭(예: 의약품 세세분류)
    assert classify_ksic("21210") == [Sector.PHARMA_BIO]


def test_classify_ksic_ambiguous_and_empty_unmapped() -> None:
    # 264(삼성전자=통신·반도체 혼재) 등 저순도 코드는 의도적 미수록 → 미분류
    assert classify_ksic("264") == []
    assert classify_ksic("20119") == []  # 201 혼재
    assert classify_ksic("") == []
    assert classify_ksic(None) == []


def test_company_profile_ok_and_nodata_and_error() -> None:
    ok = DartClient("k", json_fetch=lambda url: {"status": "000", "induty_code": "212"})
    assert ok.company_profile("00111999")["induty_code"] == "212"

    nodata = DartClient("k", json_fetch=lambda url: {"status": "013", "message": "없음"})
    assert nodata.company_profile("x") == {}

    err = DartClient("k", json_fetch=lambda url: {"status": "020", "message": "한도초과"})
    with pytest.raises(CollectError):
        err.company_profile("x")


class _FakeDart:
    """company_profile만 흉내 — srtn별 corp_code→induty_code 매핑."""

    def __init__(self, induty_by_corp: dict[str, str]) -> None:
        self._induty = induty_by_corp

    def company_profile(self, corp_code: str) -> dict[str, Any]:
        return {"induty_code": self._induty.get(corp_code, "")}


def test_classify_untagged_grounds_and_skips_unknown(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    corp_map = {
        "003220": ("00111999", "대원제약"),
        "040350": ("00346407", "크레오에스지"),
        "388790": ("01569102", "라이콤"),
        "999999": ("", "코드없음"),  # corp_map엔 있으나 corp_code 빈값
    }
    dart = _FakeDart({"00111999": "212", "00346407": "620", "01569102": "264"})  # 264=혼재
    codes = [("003220", "대원제약"), ("040350", "크레오에스지"), ("388790", "라이콤")]
    summary = classify_untagged(store, dart, corp_map, codes, as_of="20260608")  # type: ignore[arg-type]

    assert summary.attempted == 3
    assert summary.classified == 2          # 대원제약·크레오에스지
    assert summary.unclassified == 1        # 라이콤(264 미수록)
    sm = store.sector_map(GROUNDED_SOURCE)
    assert sm["003220"] == ["pharma_bio"]
    assert sm["040350"] == ["ai_software"]
    assert "388790" not in sm               # 미분류는 sector_map에서 제외
    # 미분류도 시도 기록은 남아 재시도 스킵
    assert "388790" in store.codes_with_any_row(GROUNDED_SOURCE)
    store.close()


def test_classify_untagged_missing_corp_code_is_unclassified(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    dart = _FakeDart({})
    summary = classify_untagged(store, dart, {}, [("123456", "없는회사")], as_of="20260608")  # type: ignore[arg-type]
    assert summary.classified == 0 and summary.unclassified == 1
    assert "123456" not in store.sector_map(GROUNDED_SOURCE)
    store.close()


def test_sector_map_multi_precedence(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    # 큐레이션: 삼성전자=semiconductor / grounded: 삼성전자=telecom(틀린 등록업종) + 대원제약=pharma_bio
    store.upsert_sectors(
        [{"srtn_cd": "005930", "name": "삼성전자", "sectors": ["semiconductor"], "confidence": 0.95}],
        source="llm-cls-v1",
        as_of="20260608",
    )
    store.upsert_sectors(
        [
            {"srtn_cd": "005930", "name": "삼성전자", "sectors": ["telecom"], "confidence": 0.5},
            {"srtn_cd": "003220", "name": "대원제약", "sectors": ["pharma_bio"], "confidence": 1.0},
        ],
        source="dart-ksic-v1",
        as_of="20260608",
    )
    merged = store.sector_map_multi(("llm-cls-v1", "dart-ksic-v1"))
    assert merged["005930"] == ["semiconductor"]  # 큐레이션 우선(대형주 보존)
    assert merged["003220"] == ["pharma_bio"]      # grounded가 갭 채움
    store.close()
