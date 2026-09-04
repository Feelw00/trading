"""SCREEN-1 감사의견 수집 — DART 관측 봉투(2026-09-03) 기반 당기 판독·정정 버전·격리 테스트."""

from pathlib import Path
from typing import Any

from trading.collectors.audit import collect_audit_opinions, current_opinion, verdict_summary
from trading.collectors.status import StatusStore

FY = "2025"


def _rows(cur: str | None, prev: str | None = "적정의견", rcept: str = "20260310002820", adtor: str = "삼정회계법인") -> list[dict[str, Any]]:
    """관측 형태: 당기·전기·전전기 × 2(연결/별도 추정) — bsns_year는 라벨, 결측은 adtor '-'·adt_opinion 키 없음."""
    def row(label: str, op: str | None, core: str = "매출의 발생사실") -> dict[str, Any]:
        r: dict[str, Any] = {
            "rcept_no": rcept, "corp_cls": "Y", "corp_code": "00126380", "corp_name": "테스트",
            "bsns_year": label, "adtor": adtor if op else "-", "adt_reprt_spcmnt_matter": "-",
            "core_adt_matter": core, "stlm_dt": f"{FY}-12-31",
        }
        if op:
            r["adt_opinion"] = op
            r["emphs_matter"] = "해당사항 없음"
        return r
    return [
        row("제57기 (당기)", cur), row("제57기 (당기)", cur, "매출의 발생사실\n영업권의 손상평가"),
        row("제56기(전기)", prev), row("제56기(전기)", prev),
        row("제55기(전전기)", prev), row("제55기(전전기)", prev),
    ]


class _FakeDart:
    def __init__(self, table: dict[str, list[dict[str, Any]]], fail: str | None = None) -> None:
        self.table, self._fail = table, fail
        self.calls: list[str] = []

    def audit_opinion(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> list[dict[str, Any]]:
        self.calls.append(corp_code)
        if corp_code == self._fail:
            raise RuntimeError("020 한도 초과")
        return self.table.get(corp_code, [])


CORP = {"005930": ("C1", "삼성전자"), "073640": ("C2", "테라사이언스"), "080720": ("C3", "한국유니온제약"),
        "000001": ("C4", "미제출"), "999999": ("", "corp없음")}


def test_collect_and_current_opinion_states(tmp_path: Path) -> None:
    store = StatusStore(tmp_path / "s.sqlite")
    dart = _FakeDart({
        "C1": _rows("적정의견"),
        "C2": _rows("의견거절", adtor="대주회계법인"),
        "C3": _rows(None, prev=None),         # 전 행 adtor '-' (한국유니온제약 관측)
        "C4": [],                              # status 013 → 빈 목록
    })
    added, calls, skipped, errors = collect_audit_opinions(
        dart, store, CORP, list(CORP), fy=FY, sleeper=lambda _s: None
    )
    assert (added, calls, skipped, errors) == (18, 4, 1, [])   # 6행 × 3종(빈 응답 0행) · corp 없음 1 스킵
    assert current_opinion(store.audit_rows("005930", FY)).state == "clean"
    bad = current_opinion(store.audit_rows("073640", FY))
    assert bad.adverse and bad.opinion == "의견거절" and bad.adtor == "대주회계법인"
    un = current_opinion(store.audit_rows("080720", FY))
    assert un.state == "unaudited" and un.opinion is None and un.adtor is None
    assert current_opinion(store.audit_rows("000001", FY)).state == "missing"
    s = verdict_summary(store, FY)
    assert s["clean"] == ["005930"] and s["adverse"] == ["073640:의견거절"] and s["unaudited"] == ["080720"]
    # 재수집(같은 rcept_no) → 신규 0(멱등)
    added2, *_ = collect_audit_opinions(dart, store, CORP, ["005930"], fy=FY, sleeper=lambda _s: None)
    assert added2 == 0
    store.close()


def test_correction_filing_creates_new_version_and_latest_wins(tmp_path: Path) -> None:
    store = StatusStore(tmp_path / "s.sqlite")
    first = _FakeDart({"C1": _rows("적정의견", rcept="20260310002820")})
    collect_audit_opinions(first, store, CORP, ["005930"], fy=FY, sleeper=lambda _s: None)
    # 정정보고서(새 rcept_no)에서 당기 한 행만 한정 → 비적정으로 전환(한정은 미관측 어휘 — ≠적정의견 규칙)
    corrected = _rows("적정의견", rcept="20260814003179")
    corrected[1]["adt_opinion"] = "한정의견"
    second = _FakeDart({"C1": corrected})
    added, *_ = collect_audit_opinions(second, store, CORP, ["005930"], fy=FY, sleeper=lambda _s: None)
    assert added == 6                                   # 새 버전 append(이전 버전 보존)
    rows = store.audit_rows("005930", FY)
    assert len(rows) == 12 and {r.rcept_no for r in rows} == {"20260310002820", "20260814003179"}
    v = current_opinion(rows)
    assert v.adverse and v.opinion == "한정의견" and v.rcept_no == "20260814003179"
    store.close()


def test_failure_isolated_and_logged(tmp_path: Path) -> None:
    store = StatusStore(tmp_path / "s.sqlite")
    dart = _FakeDart({"C1": _rows("적정의견")}, fail="C2")
    added, calls, skipped, errors = collect_audit_opinions(
        dart, store, CORP, ["073640", "005930"], fy=FY, sleeper=lambda _s: None
    )
    assert added == 6 and calls == 2 and len(errors) == 1 and errors[0].startswith("073640")
    assert current_opinion(store.audit_rows("073640", FY)).state == "missing"
    store.close()


def test_untagged_term_labels_use_highest_term_as_current(tmp_path: Path) -> None:
    """전수 실측(9/3): 샘표식품형 라벨 "제10기·제9기·제8기"(당기 표기 없음) → 최상위 기수 = 당기."""
    from trading.collectors.status import mark_current, term_no

    assert term_no("제57기 (당기)") == 57 and term_no("제10기") == 10 and term_no("-") is None
    assert mark_current(["제57기 (당기)", "제57기 (당기)", "제56기(전기)", "제55기(전전기)"]) == [True, True, False, False]
    assert mark_current(["제10기", "제10기", "제9기", "제9기", "제8기", "제8기"]) == [True, True, False, False, False, False]
    assert mark_current(["-", "-", "-"]) == [False, False, False]
    assert mark_current(["제30기 (전기)", "제29기 (전전기)"]) == [False, False]   # 당기 행 자체 부재 → 없음

    store = StatusStore(tmp_path / "s.sqlite")
    rows = _rows("적정의견")
    for r, lb in zip(rows, ["제10기", "제10기", "제9기", "제9기", "제8기", "제8기"], strict=True):
        r["bsns_year"] = lb
    rows[1]["adt_opinion"] = "한정의견"
    collect_audit_opinions(_FakeDart({"C1": rows}), store, CORP, ["005930"], fy=FY, sleeper=lambda _s: None)
    v = current_opinion(store.audit_rows("005930", FY))
    assert v.adverse and v.opinion == "한정의견"                 # 최상위 기수 2행 중 하나가 비적정
    # 저장된 is_current도 같은 규칙(신규 적재분) — 읽기 재판독과 일치
    assert [r.is_current for r in store.audit_rows("005930", FY)] == [True, True, False, False, False, False]
    store.close()
