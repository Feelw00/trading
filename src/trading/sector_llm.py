"""미분류 잔존분 LLM 폴백 분류기 — ``python -m trading.sector_llm`` (PROPOSALS P-2).

grounded(``dart-ksic-v1``)·큐레이션(``manual-curated-v1``)이 못 채운 미분류 잔존분을
``claude -p`` 배치 호출로 분류한다. 혼재 KSIC(649 지주·292 장비·262 전자부품 등)는
결정론 매핑이 불가능해 회사 지식 기반 분류가 정공법이다.

CLAUDE.md 절대금지 #2 정합: 섹터 **분류는 LLM 허용 영역**(원 ``llm-cls-v1`` 이 멀티에이전트
산출) — R1/R5.5 게이트·선택 판단이 아니라 태깅 메타데이터이며, 산출은 append-only
``stock_sectors`` 에 **최후순위 소스**(``llm-fallback-v1``)로 적재되어 큐레이션·grounded를
절대 덮지 않는다(``sector_map_multi`` first-wins).

환각가드:
- **확실히 아는 회사만** 분류, 모르면 빈 배열(미분류 유지) — 프롬프트 강제 + 코드 재검증.
- 섹터 값은 29 taxonomy enum에만 귀속 — 스키마 밖 값·배치 밖 종목코드는 폐기 + 카운트.
- ``confidence < threshold`` 또는 ``basis``(한 줄 근거) 누락 → 미채택(미분류 기록).
- 모델명 하드코딩 금지 — ``SECTOR_LLM_MODEL``(→ R2_MODEL → CLAUDE_MODEL) .env 주입.
"""

import os
import sys
from dataclasses import dataclass, field, replace
from typing import Any

from trading.collectors.market import MarketStore
from trading.domains import SECTORS, Sector
from trading.llm import LLMClient, LLMError, client_from_env, complete_json
from trading.screener import SECTOR_SOURCES, ScreenConfig, screen

FALLBACK_SOURCE = "llm-fallback-v1"


@dataclass(frozen=True)
class SectorLLMConfig:
    batch_size: int = 25          # 배치당 종목 수(컨텍스트·비용 상한)
    max_batches: int = 0          # 0 = 무제한
    min_confidence: float = 0.7   # 미만은 미채택(미분류 기록)
    max_sectors: int = 2          # 종목당 다중소속 상한(주력 우선)


@dataclass(frozen=True)
class SectorLLMResult:
    attempted: int                # LLM에 물어본 종목 수
    classified: int               # 채택(섹터 부여) 종목 수
    unclassified: int             # 미채택(모름·저신뢰·근거누락) 종목 수
    rejected: int                 # 스키마 위반으로 폐기한 응답 항목 수
    batches: int
    batch_errors: list[str] = field(default_factory=list)
    by_sector: dict[str, int] = field(default_factory=dict)


# (srtn_cd, name, market)
_Stock = tuple[str, str, str | None]


def build_prompt(batch: list[_Stock]) -> str:
    """배치 → 분류 프롬프트. 허용 섹터는 29 taxonomy에서 동적 생성(하드코딩 금지)."""
    taxonomy = "\n".join(f"- {s.value}: {m.label_ko}" for s, m in SECTORS.items())
    stocks = "\n".join(f"- {cd} {name}" + (f" ({mkt})" if mkt else "") for cd, name, mkt in batch)
    return (
        "너는 한국 상장기업 섹터 분류기다. 아래 종목들을 허용 섹터로 분류하라.\n\n"
        f"허용 섹터 (값: 라벨):\n{taxonomy}\n\n"
        "규칙:\n"
        "1. **확실히 아는 회사만** 분류한다. 회사를 모르거나 주력 사업이 불확실하면 "
        "sectors를 빈 배열로 두어라. 이름에서 사업을 추측하지 마라.\n"
        f"2. sectors 값은 위 목록의 값만 사용한다. 종목당 최대 2개(주력 우선).\n"
        "3. confidence는 0~1 실수, basis는 주력 사업 한 줄 근거(확실한 것만).\n"
        "4. 종목코드가 기준이다 — 비슷한 이름의 다른 회사와 혼동하지 마라.\n"
        "5. 아래 종목 전부에 대해 항목을 내되, JSON 배열만 출력한다(다른 텍스트 금지).\n\n"
        f"종목:\n{stocks}\n\n"
        '출력 형식: [{"srtn_cd":"000000","sectors":["semiconductor"],"confidence":0.9,"basis":"주력 사업 한 줄"}]'
    )


def _validate(raw: Any, batch: list[_Stock], cfg: SectorLLMConfig) -> tuple[dict[str, dict[str, Any]], int]:
    """LLM 응답 → {srtn_cd: 검증된 항목}. 스키마 밖 값은 폐기 + 카운트(채택은 별도 임계)."""
    valid_sectors = {s.value for s in Sector}
    in_batch = {cd for cd, _, _ in batch}
    out: dict[str, dict[str, Any]] = {}
    rejected = 0
    if not isinstance(raw, list):
        return {}, 1
    for it in raw:
        if not isinstance(it, dict):
            rejected += 1
            continue
        cd = str(it.get("srtn_cd") or "")
        secs = it.get("sectors")
        if cd not in in_batch or not isinstance(secs, list):
            rejected += 1
            continue
        secs_s = [str(s) for s in secs]
        if any(s not in valid_sectors for s in secs_s):
            rejected += 1  # taxonomy 밖 값 발명 — 항목째 폐기(부분 채택으로 오염 방지)
            continue
        try:
            conf = float(it.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        out[cd] = {"sectors": secs_s[: cfg.max_sectors], "confidence": conf,
                   "basis": str(it.get("basis") or "").strip()}
    return out, rejected


def classify_unclassified(
    store: MarketStore,
    client: LLMClient,
    stocks: list[_Stock],
    *,
    config: SectorLLMConfig | None = None,
    dry_run: bool = False,
) -> SectorLLMResult:
    """미분류 종목 배치 분류 → ``llm-fallback-v1`` 적재. 실패 배치는 건너뛰고 계속."""
    cfg = config or SectorLLMConfig()
    todo = sorted(stocks)  # 결정론 순서
    batches = [todo[i : i + cfg.batch_size] for i in range(0, len(todo), cfg.batch_size)]
    if cfg.max_batches:
        batches = batches[: cfg.max_batches]

    classified = unclassified = rejected = 0
    by_sector: dict[str, int] = {}
    errors: list[str] = []
    attempted = 0
    as_of = store.latest_date() or ""
    for i, batch in enumerate(batches, 1):
        try:
            raw = complete_json(client, build_prompt(batch))
        except LLMError as e:
            errors.append(f"batch {i}/{len(batches)}: {e}")
            continue  # 실패 배치는 시도 기록도 남기지 않는다(다음 실행에서 재시도)
        parsed, rej = _validate(raw, batch, cfg)
        rejected += rej
        attempted += len(batch)
        items: list[dict[str, Any]] = []
        for cd, name, _ in batch:
            got = parsed.get(cd)
            adopt = bool(got and got["sectors"] and got["basis"] and got["confidence"] >= cfg.min_confidence)
            if adopt:
                assert got is not None
                items.append({"srtn_cd": cd, "name": name, "sectors": got["sectors"],
                              "confidence": got["confidence"]})
                classified += 1
                for s in got["sectors"]:
                    by_sector[s] = by_sector.get(s, 0) + 1
            else:
                items.append({"srtn_cd": cd, "name": name, "sectors": [], "confidence": 0.0})
                unclassified += 1
        if not dry_run:
            store.upsert_sectors(items, source=FALLBACK_SOURCE, as_of=as_of)
        print(f"  batch {i}/{len(batches)}: 채택 {sum(1 for it in items if it['sectors'])}/{len(batch)}")
    return SectorLLMResult(attempted, classified, unclassified, rejected, len(batches), errors, by_sector)


def client_for_sectors(env: dict[str, str] | None = None) -> LLMClient:
    """SECTOR_LLM_MODEL 우선, 없으면 client_from_env(R2_MODEL → CLAUDE_MODEL) 그대로."""
    e = env if env is not None else dict(os.environ)
    base = client_from_env(e)
    model = e.get("SECTOR_LLM_MODEL")
    return replace(base, model=model) if model else base


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in args
    limit = 0
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    store = MarketStore()
    res = screen(store, ScreenConfig(top_n=1_000_000))
    if not res.candidates:
        print("게이트 통과 종목 없음 — 스킵")
        store.close()
        return 0
    tagged = set(store.sector_map_multi(SECTOR_SOURCES))
    attempted = store.codes_with_any_row(FALLBACK_SOURCE)
    todo: list[_Stock] = [
        (c.srtn_cd, c.name, c.market)
        for c in res.candidates
        if c.srtn_cd not in tagged and c.srtn_cd not in attempted
    ]
    if limit:
        todo = todo[:limit]
    if not todo:
        print(f"LLM 폴백 불필요 — 게이트 {res.universe}종목 모두 태깅/시도됨")
        store.close()
        return 0
    print(f"LLM 폴백 분류 대상 {len(todo)}종목 (게이트 {res.universe} · dry_run={dry_run})")
    out = classify_unclassified(store, client_for_sectors(), todo, dry_run=dry_run)
    store.close()
    print(
        f"섹터 폴백(llm-fallback-v1) as_of={res.as_of}: 시도 {out.attempted} · "
        f"채택 {out.classified} · 미분류유지 {out.unclassified} · 폐기 {out.rejected}"
    )
    for sec, n in sorted(out.by_sector.items(), key=lambda x: -x[1]):
        print(f"  {sec}: {n}")
    for err in out.batch_errors:
        print(f"⚠️ {err}")
    return 1 if out.batch_errors and not out.attempted else 0


__all__ = [
    "FALLBACK_SOURCE",
    "SectorLLMConfig",
    "SectorLLMResult",
    "build_prompt",
    "classify_unclassified",
    "client_for_sectors",
]


if __name__ == "__main__":
    raise SystemExit(main())
