"""심사 원장(Review Ledger, v2.4 — 운영자 결재 2026-09-01) — `python -m trading.review`.

코어 진입 종목에 대한 운영자 판정을 append-only로 박제한다. 헌장 2 정합:
이것은 재량 "심사"가 아니라 **규칙이 아직 못 잡는 예외에 대한 veto 채널 + 그 veto의
규칙화 루프**다. 판정은 파이프라인 게이트에 입력되지 않으며(절대금지 2와 무관한 운영자
채널), /picks 제안 바스켓에서만 소비된다(veto = 바스켓 제외, §6 R5 구현 후엔 R5 입력).

- 3상태: approved / vetoed(사유 태그 필수) / hold(확인 조건 명시)
- **자동 만료**: 판정은 결정 시점의 최신 연간 재무 연도(basis_year)를 박제 — fins에
  더 새 연간이 적재되면 판정은 만료(=pending 복귀). 낡은 판정이 새 데이터를 가리지 않는다.
- **규칙 승격 루프**: veto 태그가 임계(기본 2회) 누적되면 결정론 규칙 후보로 리포트 —
  R7+결재 상정용(v2.1 이익질·v2.3 역성장이 이 경로의 수동 선례).
"""

import sqlite3
import sys
from pathlib import Path

from trading.collectors.base import now_kst
from trading.paper import MIN_UPSIDE_PCT

DEFAULT_DB = Path("data") / "reviews.sqlite"

VERDICTS = ("approved", "vetoed", "hold")
# veto 사유 태그 화이트리스트 — 태그가 곧 규칙화의 원료(자유서술 금지)
VETO_TAGS = (
    "이익질",       # 영업외 의존 이익 (선례: v2.1)
    "역성장",       # 사업 축소 진입 (선례: v2.3)
    "지주할인",     # 비지배·중복상장 구조 (선례: COLLECT-6)
    "버킷착시",     # 섹터 버킷 이질성에 의한 극단 여력
    "복합할인",     # 무관 사업 묶음 디스카운트
    "저마진",       # 구조적 저마진(용역 등) — 회귀 상단 제한
    "유동성",       # 초소형·거래 부족
    "캡티브",       # 그룹 종속 매출
    "데이터이상",   # 공시·수집 데이터 불일치
    "이익붕괴",     # 이익 급감 진행 + 가치 갭 소멸 (2026-09-01 신설 — 대림제지·한세실업)
    "기타",
)
RULE_PROMOTION_THRESHOLD = 2   # 같은 태그 N회 → 규칙 후보 리포트

# v2.6(운영자 지시 2026-09-01: "자동으로 돌리자 — 심사도 결정적이니까") — 자동 심사 규칙.
# 헌장 2·절대금지 2 정합: 판정은 **순수 코드**(LLM 없음), 수동 판정이 있으면 자동은
# 건드리지 않는다(운영자 override 우선). veto는 자동 생성하지 않는다 — 규칙이 못 잡는
# 예외의 인간 채널로 남기고, 반복되면 태그 승격 루프로 코어 규칙이 된다(기존 설계).
AUTO_SOURCE = "auto:rule-v1"
MAX_SANE_UPSIDE_PCT = 150.0    # 초과 시 버킷 이질성·구조 할인 의심 — hold

_DDL = """
CREATE TABLE IF NOT EXISTS reviews (
  symbol TEXT NOT NULL, version INTEGER NOT NULL,
  verdict TEXT NOT NULL, tags TEXT, note TEXT, condition TEXT,
  basis_year TEXT NOT NULL, decided_at TEXT NOT NULL, source TEXT NOT NULL,
  UNIQUE(symbol, version)
);
"""


class ReviewStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_DDL)

    def close(self) -> None:
        self._conn.close()

    def decide(
        self,
        symbol: str,
        verdict: str,
        *,
        basis_year: str,
        tags: list[str] | None = None,
        note: str | None = None,
        condition: str | None = None,
        source: str = "manual:operator",
    ) -> int:
        """판정 append(새 버전) — vetoed는 태그 필수, hold는 조건 필수."""
        if verdict not in VERDICTS:
            raise ValueError(f"verdict는 {VERDICTS} 중 하나")
        if verdict == "vetoed":
            if not tags:
                raise ValueError("vetoed는 사유 태그 필수(--tag)")
            bad = [t for t in tags if t not in VETO_TAGS]
            if bad:
                raise ValueError(f"미등록 태그 {bad} — 허용: {', '.join(VETO_TAGS)}")
        if verdict == "hold" and not condition:
            raise ValueError("hold는 확인 조건 필수(--condition)")
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM reviews WHERE symbol=?", (symbol,)
        ).fetchone()
        version = int(row[0]) + 1
        self._conn.execute(
            "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?)",
            (
                symbol, version, verdict, ",".join(tags or []), note, condition,
                basis_year, now_kst().isoformat(), source,
            ),
        )
        self._conn.commit()
        return version

    def current(self, symbol: str, latest_annual_year: str) -> dict[str, str] | None:
        """유효 판정 — basis_year < 최신 연간이면 만료(None = pending 재심사)."""
        row = self._conn.execute(
            "SELECT verdict, tags, note, condition, basis_year, decided_at FROM reviews "
            "WHERE symbol=? ORDER BY version DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        rec = {
            "verdict": str(row[0]), "tags": str(row[1] or ""), "note": str(row[2] or ""),
            "condition": str(row[3] or ""), "basis_year": str(row[4]),
            "decided_at": str(row[5]),
        }
        if rec["basis_year"] < latest_annual_year:
            return None  # 새 연간 재무 적재 — 판정 만료, 재심사 대기
        return rec

    def all_current(self, latest_annual_year: str) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for (sym,) in self._conn.execute("SELECT DISTINCT symbol FROM reviews"):
            rec = self.current(str(sym), latest_annual_year)
            if rec is not None:
                out[str(sym)] = rec
        return out

    def veto_tag_counts(self) -> dict[str, int]:
        """전 이력 veto 태그 누적(만료 무관 — 패턴은 사라지지 않는다) — 규칙 승격 원료."""
        counts: dict[str, int] = {}
        for (tags,) in self._conn.execute(
            "SELECT tags FROM reviews WHERE verdict='vetoed' AND tags != ''"
        ):
            for t in str(tags).split(","):
                counts[t] = counts.get(t, 0) + 1
        return counts


def auto_review(store: ReviewStore, year: str) -> tuple[int, int]:
    """무판정 코어 종목을 결정론 규칙으로 자동 판정 — (approved 수, hold 수) 반환.

    approve = 영업 이익방향 ≥ 0 ∧ 매출급감 없음 ∧ 여력 정상 대역(**+30% ≤** … ≤ +150%) ∧ 리츠 아님.
    아니면 hold(확인 조건 명시). 여력 하한은 운영자 지시(2026-09-02): 실현 예상 수익 < +30%는
    승인 종목에 나오지 않는다 — 노출 게이트(picks.effective_verdict)와 같은 상수. 이미 유효 판정(수동·자동 불문)이 있으면 스킵 —
    만료(새 연간)되면 다음 실행이 재판정한다.
    """
    from trading.web.picks import _build_picks

    n_appr = n_hold = 0
    for pk in _build_picks():
        if pk.verdict is not None:
            continue  # 유효 판정 존재(수동 우선 포함) — 자동 미개입
        cond: str | None = None
        if pk.rec.industry == "리츠":
            cond = "리츠 분배금 관측 경로 확보 후 심사(COLLECT-5 ①)"
        elif any("매출급감" in f for f in pk.flags):
            cond = "매출 급감 원인 규명·회복 확인"
        elif pk.roe_delta is None:
            cond = "영업이익 방향 관측 부족 — 연간 재무 축적 확인"
        elif pk.roe_delta < 0:
            cond = "이익방향(영업) 양전 확인"
        elif pk.upside_pct is None or pk.upside_pct > MAX_SANE_UPSIDE_PCT:
            cond = "극단 회귀 여력 — 버킷 이질성·구조 할인 원인 확인"
        elif pk.upside_pct < MIN_UPSIDE_PCT:
            cond = f"회귀 여력 +{MIN_UPSIDE_PCT:.0f}% 회복(현재 {pk.upside_pct:+.0f}% — 실현 예상 수익 부족)"
        if cond is None:
            store.decide(
                pk.rec.symbol, "approved", basis_year=year,
                note=f"자동 승인 — 이익방향 {pk.roe_delta:+.1f}%p·여력 {pk.upside_pct:+.0f}%",
                source=AUTO_SOURCE,
            )
            n_appr += 1
        else:
            store.decide(
                pk.rec.symbol, "hold", basis_year=year, condition=cond, source=AUTO_SOURCE
            )
            n_hold += 1
    return n_appr, n_hold


def latest_annual_year() -> str:
    """fins 최신 연간(11011) 연도 — 판정 만료 기준."""
    from trading.collectors.fins import FinStore

    fs = FinStore()
    try:
        return fs.latest_annual_year()
    finally:
        fs.close()


def _core_symbols() -> list[str]:
    """현재 코어 풀 심볼(심사 대상 큐의 모집단) — /picks와 동일 판정."""
    from trading.web.picks import _build_picks

    return [p.rec.symbol for p in _build_picks()]


def main() -> int:
    args = sys.argv[1:]
    year = latest_annual_year()
    store = ReviewStore()
    try:
        if args and args[0] == "auto":
            n_appr, n_hold = auto_review(store, year)
            print(f"자동 심사(rule-v1): 승인 {n_appr} · 조건부 {n_hold} "
                  f"(기준연도 {year}, 수동 판정 우선)")
            return 0

        if not args or args[0] == "list":
            current = store.all_current(year)
            core = _core_symbols()
            pending = [s for s in core if s not in current]
            print(f"심사 원장 (만료 기준: 연간 {year}) — 코어 {len(core)}종")
            print(f"  대기(pending): {len(pending)}종 — {', '.join(pending) or '없음'}")
            for sym, rec in sorted(current.items()):
                mark = {"approved": "✔", "vetoed": "✖", "hold": "⏸"}[rec["verdict"]]
                extra = rec["tags"] or rec["condition"] or rec["note"]
                print(f"  {mark} {sym} [{rec['verdict']}] {extra[:60]}")
            counts = store.veto_tag_counts()
            promo = {t: n for t, n in counts.items() if n >= RULE_PROMOTION_THRESHOLD}
            if promo:
                print("규칙 승격 후보(veto 태그 누적 — R7+결재 상정 대상):")
                for t, n in sorted(promo.items(), key=lambda x: -x[1]):
                    print(f"  · {t} × {n}")
            return 0

        symbol, verdict = args[0], args[1] if len(args) > 1 else ""
        if verdict == "approved":
            # 운영자 지시(2026-09-02): 실현 예상 수익(회귀 여력) < +30%는 승인 종목 제외 —
            # 수동 승인도 같은 하한. 노출 게이트가 어차피 보류시키므로 모순 기록을 막는다.
            from trading.web.picks import _build_picks

            pk = next((p for p in _build_picks() if p.rec.symbol == symbol), None)
            if pk is not None and not pk.upside_ok:
                cur = "결측" if pk.upside_pct is None else f"{pk.upside_pct:+.0f}%"
                print(f"{symbol}: 회귀 여력 {cur} < +{MIN_UPSIDE_PCT:.0f}% — 승인 불가(실현 예상 "
                      "수익 부족). `hold --condition '회귀 여력 +30% 회복'`으로 남겨라.",
                      file=sys.stderr)
                return 2
        tags: list[str] = []
        note = condition = None
        it = iter(args[2:])
        for a in it:
            if a == "--tag":
                tags.append(next(it))
            elif a == "--note":
                note = next(it)
            elif a == "--condition":
                condition = next(it)
        v = store.decide(
            symbol, verdict, basis_year=year, tags=tags, note=note, condition=condition
        )
        print(f"{symbol} → {verdict} (v{v}, 기준연도 {year}) 박제")
        return 0
    except (ValueError, StopIteration) as e:
        print(f"오류: {e}", file=sys.stderr)
        print("사용법: python -m trading.review [list | <symbol> approved|vetoed|hold "
              "[--tag T]... [--note N] [--condition C]]", file=sys.stderr)
        return 1
    finally:
        store.close()


__all__ = [
    "AUTO_SOURCE",
    "DEFAULT_DB",
    "RULE_PROMOTION_THRESHOLD",
    "MAX_SANE_UPSIDE_PCT",
    "VETO_TAGS",
    "VERDICTS",
    "ReviewStore",
    "auto_review",
    "latest_annual_year",
]


if __name__ == "__main__":
    raise SystemExit(main())
