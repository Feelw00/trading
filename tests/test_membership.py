"""토스 스냅샷 멤버십 파생(policy-v1.5, PIVOT-10) 검증."""

from trading.cycle.membership import build_curated_groups, snapshot_as_of, snapshot_names
from trading.cycle.policy import (
    CURATED_GROUPS,
    CURATION_ADD,
    CURATION_REMOVE,
    FINANCIAL_PROFILE_GROUPS,
    TOSS_SELECTORS,
    WHITELIST,
)


def test_snapshot_loads_and_codes_are_wellformed() -> None:
    names = snapshot_names()
    assert len(names) > 700  # 42파일 고유 종목
    assert all(len(cd) == 6 for cd in names)
    assert snapshot_as_of() == "2026-08-31"


def test_selector_basic_and_wildcard() -> None:
    groups = build_curated_groups({"조선사만": (("조선", "조선사"),), "금속전체": (("금속", "*"),)})
    assert "010140" in groups["조선사만"]  # 삼성중공업
    assert "009540" not in groups["조선사만"]  # HD한국조선해양은 지주사 태깅 — add 없이는 미편입
    assert {"005490", "010130", "006110"} <= set(groups["금속전체"])  # 철강·아연·알루미늄 전부


def test_overrides_add_remove() -> None:
    groups = build_curated_groups(
        {"g": (("조선", "조선사"),)}, add={"g": ("009540",)}, remove={"g": ("010140",)}
    )
    assert "009540" in groups["g"] and "010140" not in groups["g"]


def test_policy_v15_whitelist_groups_all_curated() -> None:
    assert set(WHITELIST.values()) == set(CURATED_GROUPS)
    # "증권" 버킷은 계측 전용으로 프로파일 유지(v1.4 지위) — 큐레이션 키만 그룹 존재 요구
    curated_profiles = {g for g in FINANCIAL_PROFILE_GROUPS if g.endswith("(큐레이션)")}
    assert curated_profiles <= set(CURATED_GROUPS)
    for group, codes in CURATED_GROUPS.items():
        assert codes, group
        assert len(codes) == len(set(codes)), f"{group} 중복 코드"


def test_policy_v15_operator_confirmed_overrides_kept() -> None:
    """v1.3 운영자 확정이 토스 태깅보다 우선(오버라이드 보존)."""
    assert "009540" in CURATED_GROUPS["조선(큐레이션)"]  # HD한국조선해양 →조선
    assert {"096770", "010950", "078930"} == set(CURATED_GROUPS["정유(큐레이션)"])  # 정유 3사 유지
    bank = set(CURATED_GROUPS["은행(큐레이션)"])
    assert "138040" not in bank and "071050" not in bank  # 메리츠·한국금융지주 은행 제외
    chem = set(CURATED_GROUPS["화학(큐레이션)"])
    assert "096770" not in chem and "010950" not in chem  # 정유↔화학 분리 유지


def test_policy_v15_purity_fixes() -> None:
    """P-17 A항 혼합 왜곡 교정 — 버킷 오염원이 큐레이션에서 자연 이탈."""
    assert "079550" not in CURATED_GROUPS["금속(큐레이션)"]  # LIG넥스원(방산)
    machinery = set(CURATED_GROUPS["건설기계(큐레이션)"])
    assert "042700" not in machinery  # 한미반도체(반도체장비)
    assert "034020" not in machinery  # 두산에너빌리티(원전·화력)
    assert {"241560", "267270", "042670"} <= machinery  # 두산밥캣·HD건설기계·HD현대인프라코어
    shipping = set(CURATED_GROUPS["해운·물류(큐레이션)"])
    assert "003490" not in shipping  # 대한항공(항공)
    assert {"011200", "028670"} <= shipping  # HMM·팬오션


def test_policy_v15_multi_membership_natural() -> None:
    """다중 소속 자연 허용(운영자 원칙 2026-08-31)."""
    assert "003670" in CURATED_GROUPS["금속(큐레이션)"]  # 포스코퓨처엠 — 철강 태깅
    assert "003670" in CURATED_GROUPS["화학(큐레이션)"]  # 동시에 화학원료 태깅


def test_selectors_reference_existing_theme_categories() -> None:
    """셀렉터 오타 가드 — 스냅샷에 실존하는 (테마, 카테고리)만 참조."""
    import json
    from importlib.resources import files

    from trading.cycle.membership import SNAPSHOT_NAME

    raw = json.loads(
        files("trading.cycle").joinpath("data").joinpath(SNAPSHOT_NAME).read_text("utf-8")
    )
    pairs = {(r["theme"], r["category"]) for r in raw["rows"]}
    themes = {t for t, _c in pairs}
    for sels in TOSS_SELECTORS.values():
        for theme, category in sels:
            if category == "*":
                assert theme in themes, theme
            else:
                assert (theme, category) in pairs, (theme, category)
    # 오버라이드 코드 형식
    for mapping in (CURATION_ADD, CURATION_REMOVE):
        for codes in mapping.values():
            assert all(len(cd) == 6 for cd in codes)
