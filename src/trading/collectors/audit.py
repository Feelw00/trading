"""감사의견 수집 — DART 회계감사인의 명칭 및 감사의견(accnutAdtorNmNdAdtOpinion) → data/status.sqlite (SCREEN-1).

공식 가이드 DS002 #13(apiId 2020009), 2026-09-03 실호출 관측(`docs/research/2026-09-03-screen1-status-audit-sources.md`):
- 응답 6행 = 당기·전기·전전기 × 2(연결/별도로 추정 — 구분 필드 없음, CJ대한통운은 core_adt_matter만 상이).
  규칙: **당기 행 전부**를 본다(어느 한 행이라도 비적정이면 비적정).
- ``bsns_year``는 연도가 아니라 "제57기 (당기)" 라벨(공백·개행 변형) → "(당기)" 포함 행이 요청 사업연도.
  전수 실측(9/3)에서 "(당기)" 없이 "제10기·제9기·제8기"만 쓰는 회사(샘표식품 등) 발견 → 최상위 기수 = 당기(`mark_current`).
- ``adt_opinion`` 어휘: '적정의견' · '의견거절' · None(``adtor == '-'`` = 감사보고서 없음/미제출).
  한정·부적정은 미관측 — "≠ '적정의견'"을 비적정으로 보수 처리(추측 어휘 매칭 금지).
- ``rcept_no``는 최신 접수분(정정 포함 추정) → 같은 rcept_no 재수집은 무시, 새 rcept_no = 새 버전(append-only).
판정 게이트 적용은 운영자 결재 (a)(2026-09-03) — 배선은 `screen/`. 여기는 수집 + 순수 요약(`current_opinion`)만.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from trading.collectors.base import now_kst
from trading.collectors.status import OPINION_CLEAN, AuditRow, StatusStore, mark_current

AUDIT_REPRT_ANNUAL = "11011"


class AuditClient(Protocol):
    def audit_opinion(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class AuditVerdict:
    """(symbol, fy) 최신 접수분의 당기 감사의견 요약 — 순수 산출.

    state: clean(당기 전 행 적정의견) · adverse(당기 어느 행이든 ≠ 적정의견) ·
           unaudited(행은 있으나 당기 의견 없음 — adtor '-' = 감사보고서 미제출/미감사) · missing(수집 행 없음)
    """

    state: str
    opinion: str | None
    rcept_no: str | None
    adtor: str | None

    @property
    def adverse(self) -> bool:
        return self.state == "adverse"


def current_opinion(rows: Sequence[AuditRow]) -> AuditVerdict:
    if not rows:
        return AuditVerdict(state="missing", opinion=None, rcept_no=None, adtor=None)
    latest = max(r.rcept_no for r in rows)  # 14자리 접수번호 = 시간순 문자열 비교, '' 는 최하위
    lr = sorted((r for r in rows if r.rcept_no == latest), key=lambda r: r.row_idx)
    # 당기 판독은 저장값(is_current)이 아니라 라벨에서 매번 재판독 — 규칙 개선(샘표식품형 "제10기" 라벨)이
    # 재수집 없이 기존 행에 적용된다(append-only 원칙: 저장 행은 불변).
    flags = mark_current([r.term_label for r in lr])
    cur = [r for r, f in zip(lr, flags, strict=True) if f]
    opinions = sorted({r.adt_opinion for r in cur if r.adt_opinion})
    adtor = next((r.adtor for r in cur if r.adtor and r.adtor != "-"), None)
    if not opinions:
        return AuditVerdict(state="unaudited", opinion=None, rcept_no=latest or None, adtor=adtor)
    bad = [o for o in opinions if o != OPINION_CLEAN]
    if bad:
        return AuditVerdict(state="adverse", opinion="·".join(bad), rcept_no=latest, adtor=adtor)
    return AuditVerdict(state="clean", opinion=OPINION_CLEAN, rcept_no=latest, adtor=adtor)


def collect_audit_opinions(
    dart: AuditClient,
    store: StatusStore,
    corp_map: Mapping[str, tuple[str, str]],
    symbols: Sequence[str],
    *,
    fy: str,
    reprt_code: str = AUDIT_REPRT_ANNUAL,
    sleeper: Callable[[float], None] = time.sleep,
    pace_s: float = 0.05,
) -> tuple[int, int, int, list[str]]:
    """(신규 행, 호출 수, corp 미등재 스킵, 오류). 한 종목 실패가 나머지를 막지 않는다."""
    fetched_at = now_kst().isoformat()
    added = calls = skipped = 0
    errors: list[str] = []
    for sym in symbols:
        ent = corp_map.get(sym)
        if not ent or not ent[0]:
            skipped += 1
            continue
        calls += 1
        try:
            rows = dart.audit_opinion(ent[0], fy, reprt_code)
        except Exception as exc:  # noqa: BLE001 — 격리(한도·일시 오류)
            errors.append(f"{sym}: {exc}")
            store.log_audit_fetch(sym, fy, reprt_code, fetched_at=fetched_at, status="error", n_rows=0)
            continue
        n = store.append_audit(sym, ent[0], fy, reprt_code, rows, fetched_at=fetched_at)
        store.log_audit_fetch(sym, fy, reprt_code, fetched_at=fetched_at, status="ok" if rows else "empty", n_rows=len(rows))
        added += n
        if pace_s > 0:
            sleeper(pace_s)
    return added, calls, skipped, errors


def verdict_summary(store: StatusStore, fy: str, reprt_code: str = AUDIT_REPRT_ANNUAL) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"clean": [], "adverse": [], "unaudited": [], "missing": []}
    for sym in sorted(store.audit_symbols(fy, reprt_code)):
        v = current_opinion(store.audit_rows(sym, fy, reprt_code))
        out[v.state].append(f"{sym}:{v.opinion}" if v.state == "adverse" else sym)
    return out


def main(argv: list[str] | None = None) -> int:
    """`python -m trading.collectors.audit [--fy 2025] [--limit N] [--symbols …]` — 재무 유니버스 감사의견."""
    import os
    import sys

    from trading.collectors.dart import DartClient
    from trading.collectors.fins import FinStore

    args = list(sys.argv[1:] if argv is None else argv)
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 미설정 — 감사의견 수집 blocked")
        return 1
    dart = DartClient(key)
    fs = FinStore()
    try:
        fy = args[args.index("--fy") + 1] if "--fy" in args else fs.latest_annual_year()
        symbols = args[args.index("--symbols") + 1:] if "--symbols" in args else sorted(fs.symbols())
    finally:
        fs.close()
    if "--limit" in args:
        symbols = symbols[: int(args[args.index("--limit") + 1])]
    corp_map = dart.corp_code_map()
    store = StatusStore()
    try:
        added, calls, skipped, errors = collect_audit_opinions(dart, store, corp_map, symbols, fy=fy)
        summary = verdict_summary(store, fy)
    finally:
        store.close()
    print(f"감사의견 수집(SCREEN-1, DART, FY{fy} 사업보고서): 대상 {len(symbols)} · 호출 {calls} · 신규 행 {added} · "
          f"corp 미등재 {skipped} · 오류 {len(errors)} → data/status.sqlite")
    print(f"  적정 {len(summary['clean'])} · 비적정 {len(summary['adverse'])} · 당기 의견 없음 {len(summary['unaudited'])}"
          f" — 필터 적용은 R4(운영자 결재 (a) 2026-09-03)")
    for s in summary["adverse"][:20]:
        print(f"  ✖ {s}")
    for e in errors[:5]:
        print(f"  ⚠️ {e}")
    # 전수 실패(키·한도·네트워크)만 rc=1 — 부분 오류는 로그·P1 없이 계속(best-effort 수집)
    return 1 if calls and len(errors) == calls else 0


__all__ = [
    "AUDIT_REPRT_ANNUAL",
    "AuditClient",
    "AuditVerdict",
    "collect_audit_opinions",
    "current_opinion",
    "main",
    "verdict_summary",
]


if __name__ == "__main__":
    raise SystemExit(main())
