"""grounded 섹터 분류(DART 업종 → KSIC 크로스워크) + 회사개황 파싱 + 스크리너 병합."""

from pathlib import Path
from typing import Any

import pytest

from trading.collectors.base import CollectError
from trading.collectors.dart import DartClient
from trading.collectors.market import MarketStore
from trading.domains import Sector
from trading.sectors import (
    KRX_NONE_SOURCE,
    KRX_SOURCE,
    MANUAL_SECTORS,
    MANUAL_SOURCE,
    GROUNDED_SOURCE,
    apply_manual_overrides,
    classify_krx,
    classify_ksic,
    classify_untagged,
    krx_todo,
)


def test_classify_ksic_high_purity_codes() -> None:
    assert classify_ksic("212") == [Sector.PHARMA_BIO]      # 대원제약
    assert classify_ksic("620") == [Sector.AI_SOFTWARE]     # 크레오에스지
    assert classify_ksic("261") == [Sector.SEMICONDUCTOR]


def test_classify_ksic_uses_three_digit_prefix() -> None:
    # 5자리 입력도 3자리 규칙으로 매칭(예: 의약품 세세분류)
    assert classify_ksic("21210") == [Sector.PHARMA_BIO]


def test_classify_ksic_p1_expansion_buckets() -> None:
    # P-1(2026-07-11) 실측 채택: 해운·물류 / 운송 / 레저·카지노
    assert classify_ksic("50112") == [Sector.SHIPPING_LOGISTICS]  # HMM·팬오션·흥아해운
    assert classify_ksic("5299") == [Sector.SHIPPING_LOGISTICS]   # 현대글로비스(물류)
    assert classify_ksic("511") == [Sector.TRANSPORT]             # 대한항공(항공여객)
    assert classify_ksic("49220") == [Sector.TRANSPORT]           # 동양고속(시외버스)
    assert classify_ksic("91249") == [Sector.LEISURE_CASINO]      # 강원랜드·파라다이스·GKL
    assert classify_ksic("75210") == [Sector.LEISURE_CASINO]      # 롯데관광개발(여행)
    # 91249는 5자리 정밀 매칭 — 912 일반(기타 오락)은 미채택 유지
    assert classify_ksic("91221") == []


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


def test_manual_overrides_idempotent_and_valid(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    n = apply_manual_overrides(store, as_of="20260608")
    assert n == sum(len(secs) for _, secs in MANUAL_SECTORS.values())  # 섹터별 1행
    assert apply_manual_overrides(store, as_of="20260608") == 0  # 멱등
    sm = store.sector_map(MANUAL_SOURCE)
    assert sm["011170"] == ["chemicals"]              # 롯데케미칼
    assert set(sm["139130"]) == {"financials", "holding"}  # iM금융지주 다중소속
    # 모든 오버라이드 섹터는 유효 taxonomy 값
    valid = {s.value for s in Sector}
    for _, secs in MANUAL_SECTORS.values():
        assert all(s.value in valid for s in secs)
    store.close()


def test_manual_takes_precedence_over_grounded(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    apply_manual_overrides(store, as_of="20260608")
    # grounded가 같은 종목을 다르게 분류해도 manual이 이김
    store.upsert_sectors(
        [{"srtn_cd": "011170", "name": "롯데케미칼", "sectors": ["battery_materials"], "confidence": 0.5}],
        source=GROUNDED_SOURCE,
        as_of="20260608",
    )
    merged = store.sector_map_multi((MANUAL_SOURCE, "llm-cls-v1", GROUNDED_SOURCE))
    assert merged["011170"] == ["chemicals"]
    store.close()


class _FakeKis:
    """quote_price만 흉내 — 업종명 매핑 + 지정 종목 호출 실패 + 정상 응답·업종 없음(``blank``).

    실호출 관측(2026-09-04): KONEX·외국기업(950)·신형 코드는 79~80필드 정상 응답인데
    ``bstp_kor_isnm`` 이 null 또는 " " — ``blank`` 는 그 모양을 흉내낸다.
    매핑·blank·fail 어디에도 없는 코드는 필드 없는 빈 응답(일시 장애 모양)."""

    def __init__(
        self,
        bstp_by_code: dict[str, str],
        fail: set[str] | None = None,
        blank: set[str] | None = None,
    ) -> None:
        self._bstp = bstp_by_code
        self._fail = fail or set()
        self._blank = blank or set()
        self.calls: list[str] = []

    def quote_price(self, srtn_cd: str) -> dict[str, Any]:
        self.calls.append(srtn_cd)
        if srtn_cd in self._fail:
            raise OSError("kis down")
        if srtn_cd in self._blank:
            return {"stck_prpr": "10200", "rprs_mrkt_kor_name": "KONEX", "bstp_kor_isnm": " "}
        if srtn_cd in self._bstp:
            return {"stck_prpr": "255500", "bstp_kor_isnm": self._bstp[srtn_cd]}
        return {"bstp_kor_isnm": ""}


def test_classify_krx_tags_official_sector_and_skips_failures(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    kis = _FakeKis({"005930": "전기·전자", "105560": "은행"}, fail={"999999"}, blank={"140610"})
    codes = [
        ("005930", "삼성전자"),
        ("105560", "KB금융"),
        ("999999", "장애종목"),   # 호출 실패 → 스킵(재시도)
        ("888888", "빈응답"),     # 필드 없는 빈 응답(일시 장애 모양) → 스킵(재시도)
        ("140610", "엔솔바이오"),  # 정상 응답·업종 없음(KONEX) → 'none' 박제(P-19 ④)
    ]
    s = classify_krx(store, kis, codes, as_of="20260713")  # type: ignore[arg-type]

    assert s.attempted == 5
    assert s.classified == 2
    assert s.unclassified == 2  # 재시도 예정 스킵분(장애·빈 응답)
    assert s.pinned == 1
    sm = store.sector_map(KRX_SOURCE)
    assert sm["005930"] == ["전기·전자"]  # 거래소 원문 그대로(정규화 없음)
    assert sm["105560"] == ["은행"]
    # 스킵분은 행을 남기지 않아 다음 실행에 재시도된다(일시 장애의 영구화 방지)
    assert "999999" not in store.codes_with_any_row(KRX_SOURCE)
    assert "888888" not in store.codes_with_any_row(KRX_SOURCE)
    assert "888888" not in store.codes_with_any_row(KRX_NONE_SOURCE)
    # 박제분: kis-bstp-v1 행은 없고(업종 추측 없음) 'none' 소스의 unclassified 행만 남는다
    assert "140610" not in store.codes_with_any_row(KRX_SOURCE)
    assert store.codes_with_any_row(KRX_NONE_SOURCE) == {"140610"}
    assert store.sector_map(KRX_NONE_SOURCE) == {}  # unclassified는 어떤 섹터 맵에도 안 나옴
    assert store.sector_map_multi((KRX_SOURCE, KRX_NONE_SOURCE)).get("140610") is None
    store.close()


def test_classify_krx_pin_source_none_keeps_legacy_skip(tmp_path: Path) -> None:
    # pin_source=None이면 정상 응답·업종 없음도 종전처럼 행 없이 스킵(재시도)
    store = MarketStore(tmp_path / "m.sqlite")
    kis = _FakeKis({}, blank={"140610"})
    s = classify_krx(store, kis, [("140610", "엔솔바이오")], as_of="20260713", pin_source=None)  # type: ignore[arg-type]
    assert (s.classified, s.unclassified, s.pinned) == (0, 1, 0)
    assert store.codes_with_any_row(KRX_NONE_SOURCE) == set()
    store.close()


def test_krx_todo_excludes_pinned_and_retries_only_on_flag(tmp_path: Path) -> None:
    """P-19 ④: 박제분은 기본 실행에서 제외(콜 절감), --retry-pinned에서만 재시도.
    이미 kis-bstp-v1 행이 생긴 종목(재시도 성공분)은 어느 모드에서도 제외. 상장폐지분(names 밖)도 제외."""
    store = MarketStore(tmp_path / "m.sqlite")
    store.upsert_sectors(
        [{"srtn_cd": "005930", "name": "삼성전자", "sectors": ["전기·전자"], "confidence": 1.0}],
        source=KRX_SOURCE, as_of="20260713",
    )
    store.upsert_sectors(
        [
            {"srtn_cd": "140610", "name": "엔솔바이오", "sectors": [], "confidence": 0.0},
            {"srtn_cd": "950160", "name": "코오롱티슈진", "sectors": [], "confidence": 0.0},
            {"srtn_cd": "000000", "name": "상폐종목", "sectors": [], "confidence": 0.0},
        ],
        source=KRX_NONE_SOURCE, as_of="20260904",
    )
    # 950160은 나중 재시도로 태깅 성공했다고 가정 → kis-bstp-v1 행도 있음
    store.upsert_sectors(
        [{"srtn_cd": "950160", "name": "코오롱티슈진", "sectors": ["제약"], "confidence": 1.0}],
        source=KRX_SOURCE, as_of="20260911",
    )
    names = {"005930": "삼성전자", "140610": "엔솔바이오", "950160": "코오롱티슈진", "0001A0": "덕양에너젠"}

    assert krx_todo(store, names) == [("0001A0", "덕양에너젠")]          # 미시도만
    assert krx_todo(store, names, retry_pinned=True) == [("140610", "엔솔바이오")]  # 박제분만(성공분·상폐 제외)
    store.close()


def test_pinned_then_retagged_krx_row_wins(tmp_path: Path) -> None:
    # 박제 뒤 재시도로 업종이 생기면 kis-bstp-v1 행이 first-wins로 이긴다('none' 행은 append-only로 남음)
    store = MarketStore(tmp_path / "m.sqlite")
    kis1 = _FakeKis({}, blank={"094800"})
    classify_krx(store, kis1, [("094800", "맵스리얼티")], as_of="20260904")  # type: ignore[arg-type]
    assert krx_todo(store, {"094800": "맵스리얼티"}) == []
    kis2 = _FakeKis({"094800": "부동산"})
    s = classify_krx(store, kis2, krx_todo(store, {"094800": "맵스리얼티"}, retry_pinned=True), as_of="20260911")  # type: ignore[arg-type]
    assert s.classified == 1
    assert store.sector_map_multi((KRX_SOURCE, MANUAL_SOURCE))["094800"] == ["부동산"]
    assert krx_todo(store, {"094800": "맵스리얼티"}, retry_pinned=True) == []  # 성공분은 재시도 제외
    store.close()


def test_krx_source_takes_precedence_over_all(tmp_path: Path) -> None:
    # 운영자 결정(2026-07-13): 거래소 공식 업종이 큐레이션·taxonomy 태그를 덮는다
    store = MarketStore(tmp_path / "m.sqlite")
    store.upsert_sectors(
        [{"srtn_cd": "005930", "name": "삼성전자", "sectors": ["semiconductor"], "confidence": 0.95}],
        source="llm-cls-v1",
        as_of="20260608",
    )
    store.upsert_sectors(
        [{"srtn_cd": "005930", "name": "삼성전자", "sectors": ["전기·전자"], "confidence": 1.0}],
        source=KRX_SOURCE,
        as_of="20260713",
    )
    merged = store.sector_map_multi((KRX_SOURCE, "manual-curated-v1", "llm-cls-v1"))
    assert merged["005930"] == ["전기·전자"]
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
