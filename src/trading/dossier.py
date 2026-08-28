"""R4.5 심사 패킷 — `python -m trading.dossier` (설계서 v0.3 §3 R4.5).

R4 통과 후보마다 긍정/반박 논거를 LLM(claude -p, CLAUDE_MODEL)이 서술해 감사 기록으로
박제한다. 역할 규율:
- **판정 미개입**: 산출물은 어떤 파이프라인 판정에도 입력되지 않는다(운영자 참고·감사 전용).
- **환각 가드**: 사실 근거는 결정론 조립 ``fact_card``에 한정 — 카드 밖 수치·사실 주장 금지,
  모르는 것은 "자료 없음"으로 쓰게 프롬프트가 강제하고, bear_case는 스키마가 의무화한다.
- **멱등**: 같은 후보(candidate_ref)의 패킷이 이미 있으면 스킵(``--force``로 재서술).

산출: data/dossiers.sqlite(append-only) + ``.runtime/reports/dossiers/<일자>-<종목>.md``.
"""

import sqlite3
import sys
from pathlib import Path

from trading.collectors.base import now_kst
from trading.contracts.longterm import CandidateRecord, CycleRecord, DossierRecord, ValuationRecord
from trading.cycle.policy import WHITELIST
from trading.cycle.store import CycleStore
from trading.llm import LLMClient, client_from_env, complete_json
from trading.screen.store import CandidateStore
from trading.valuation.store import ValuationStore

DEFAULT_DB = Path("data") / "dossiers.sqlite"
REPORT_DIR = Path(".runtime") / "reports" / "dossiers"

_DDL = """
CREATE TABLE IF NOT EXISTS dossiers (
  id TEXT NOT NULL, version INTEGER NOT NULL, candidate_ref TEXT NOT NULL,
  symbol TEXT NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL, appended_at TEXT NOT NULL,
  UNIQUE(id, version)
);
"""


class DossierStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)

    def append(self, record: DossierRecord) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM dossiers WHERE id=?", (record.id,)
        ).fetchone()
        version = int(row[0]) + 1
        self._conn.execute(
            "INSERT INTO dossiers (id, version, candidate_ref, symbol, as_of, payload, appended_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                record.id,
                version,
                record.candidate_ref,
                record.symbol,
                record.as_of.isoformat(),
                record.model_dump_json(),
                now_kst().isoformat(),
            ),
        )
        self._conn.commit()
        return version

    def latest_for_symbol(self, symbol: str) -> DossierRecord | None:
        row = self._conn.execute(
            "SELECT payload FROM dossiers WHERE symbol=? ORDER BY as_of DESC, version DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return DossierRecord.model_validate_json(str(row[0])) if row else None

    def exists_for_candidate(self, candidate_ref: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM dossiers WHERE candidate_ref=? LIMIT 1", (candidate_ref,)
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self._conn.close()


def _fmt(v: float | None, spec: str = ".2f") -> str:
    return f"{v:{spec}}" if v is not None else "자료 없음"


def build_fact_card(cand: CandidateRecord, val: ValuationRecord, cyc: CycleRecord) -> str:
    """결정론 정량 카드 — LLM이 인용할 수 있는 사실의 전부(카드 밖 수치 주장 금지)."""
    ax = cyc.axes_primary
    lines = [
        f"[종목] {cand.symbol} · 산업 {cand.industry} (KRX 업종 {val.sector_krx or '자료 없음'})",
        f"[기준일] 밸류에이션 {str(val.as_of)[:10]} · 재무 기준 {val.fin_basis or '자료 없음'}",
        f"[산업 국면(R3)] {cyc.phase.value} · 온도 {cyc.temperature if cyc.temperature is not None else '자료 없음'}"
        f" · PBR밴드 {_fmt(ax.sector_pbr_band_pct, '.0%')} · 마진밴드 {_fmt(ax.sector_margin_band_pct, '.0%')}"
        f" · 매출z {_fmt(ax.sector_rev_cycle_z)} · 구조적사양 {cyc.secular_decline}",
        f"[밸류에이션] PBR {_fmt(val.pbr)} · PER {_fmt(val.per)} · PSR {_fmt(val.psr)}"
        f" · ROE {_fmt(val.roe)} · 부채비율 {_fmt(val.debt_ratio)}",
        f"[상대] 산업 내 PBR 하위 {_fmt(cand.industry_pbr_pct, '.0%')} · 섹터 내 하위 {_fmt(val.sector_pbr_pct, '.0%')}",
        f"[생존력] 최근 5년 적자 {val.loss_years_5y}년 (관측 {val.loss_years_observed}년)"
        f" · 5년 ROE 중앙값 {_fmt(val.roe_median_5y, '+.1%')}",
        f"[환원·거버넌스] 수집 전(PIVOT-3) — 자료 없음",
        f"[수급] 창 축적 중(60거래일 미만) — 미포함",
        f"[미적용 필터] {', '.join(cand.unapplied) if cand.unapplied else '없음'}",
    ]
    return "\n".join(lines)


_PROMPT = """너는 장기 사이클·가치 투자 시스템의 심사 패킷 서술자다. 아래 정량 카드의 사실만 근거로
이 후보의 긍정 논거(bull)와 반박 논거(bear)를 서술하라.

절대 규칙:
- 카드에 없는 수치·사실·뉴스·기업 세부 정보를 지어내지 마라. 카드 밖 지식이 필요한 주장은
  "자료 없음: ~확인 필요" 형태로 써라.
- bear_case는 반드시 2개 이상 — 반박이 약하면 심사 패킷이 아니다.
- 각 항목은 한국어 1~2문장. 투자 권유·목표가·매수/매도 판단 금지(이 서술은 어떤 판정에도
  입력되지 않는 참고 기록이다).

정량 카드:
{card}

JSON으로만 답하라: {{"bull_case": ["...", ...], "bear_case": ["...", ...], "risks": ["...", ...]}}"""


def write_dossier(
    client: LLMClient,
    model_label: str,
    cand: CandidateRecord,
    val: ValuationRecord,
    cyc: CycleRecord,
) -> DossierRecord:
    card = build_fact_card(cand, val, cyc)
    data = complete_json(client, _PROMPT.format(card=card))
    now = now_kst()
    return DossierRecord(
        id=f"dossier.{now.strftime('%Y%m%d')}.{cand.symbol}",
        as_of=cand.as_of,
        fetched_at=now,
        source="llm:claude-p",
        candidate_ref=cand.id,
        symbol=cand.symbol,
        industry=cand.industry,
        model=model_label,
        bull_case=[str(x) for x in data.get("bull_case", [])],
        bear_case=[str(x) for x in data.get("bear_case", [])],
        risks=[str(x) for x in data.get("risks", [])],
        fact_card=card,
    )


def render_md(d: DossierRecord) -> str:
    lines = [
        f"# 심사 패킷 — {d.symbol} [{d.industry}]",
        "",
        f"> 감사 기록(참고 전용) — 어떤 판정에도 입력되지 않는다. 서술 모델 {d.model},"
        f" 후보 {d.candidate_ref}, 작성 {str(d.fetched_at)[:16]}",
        "",
        "## 긍정 논거 (bull)",
        *[f"- {x}" for x in d.bull_case],
        "",
        "## 반박 논거 (bear — 의무)",
        *[f"- {x}" for x in d.bear_case],
        "",
        "## 리스크",
        *([f"- {x}" for x in d.risks] or ["- (없음)"]),
        "",
        "## 정량 카드 (서술의 사실 근거 전부)",
        "```",
        d.fact_card,
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import os

    args = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in args

    if not (os.environ.get("R2_MODEL") or os.environ.get("CLAUDE_MODEL")):
        print("CLAUDE_MODEL 미설정 — 심사 패킷 서술 blocked(.env 주입 필요)")
        return 0

    cand_store, val_store, cyc_store, store = (
        CandidateStore(), ValuationStore(), CycleStore(), DossierStore(),
    )
    try:
        client = client_from_env()
        model_label = client.model or "claude-default"
        passed = cand_store.latest_passed()
        if not passed:
            print("통과 후보 없음 — 심사 패킷 대상 없음")
            return 0
        written = skipped = failed = 0
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        for cand in passed:
            if not force and store.exists_for_candidate(cand.id):
                skipped += 1
                continue
            val = val_store.latest_for_symbol(cand.symbol)
            group = WHITELIST.get(cand.industry, cand.industry)
            cyc = cyc_store.latest_for_industry(group)
            if val is None or cyc is None:
                print(f"⚠️ {cand.symbol}: 정량 카드 재료 결측(valuation/cycle) — 스킵")
                failed += 1
                continue
            try:
                d = write_dossier(client, model_label, cand, val, cyc)
            except Exception as exc:  # noqa: BLE001 — 한 후보 실패가 나머지를 막지 않는다
                print(f"⚠️ {cand.symbol}: 서술 실패 — {exc}")
                failed += 1
                continue
            store.append(d)
            out = REPORT_DIR / f"{now_kst().strftime('%Y%m%d')}-{d.symbol}.md"
            out.write_text(render_md(d), encoding="utf-8")
            print(f"박제: {d.symbol} bull {len(d.bull_case)}·bear {len(d.bear_case)}·risks {len(d.risks)} → {out}")
            written += 1
        print(f"심사 패킷: 작성 {written} · 스킵(기존) {skipped} · 실패 {failed}")
    finally:
        cand_store.close()
        val_store.close()
        cyc_store.close()
        store.close()
    return 0


__all__ = ["DossierStore", "build_fact_card", "render_md", "write_dossier", "DEFAULT_DB"]


if __name__ == "__main__":
    raise SystemExit(main())
