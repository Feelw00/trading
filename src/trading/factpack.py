"""FactPack 조립 — 후보별 grounded 입력 슬라이스(R3 페르소나 분석의 입력).

**결정론**: 스크리너 후보 → 가격맥락(DB) + 공시·재무(DART)를 묶어 ``FactPack`` 으로 직렬화.
LLM 미개입. 없는 데이터는 ``notes`` 에 사유만 남기고 **지어내지 않는다**(환각가드).
출력: ``.runtime/factpack/<거래일>/<srtn_cd>_<name>.json`` (gitignored, append-only 성격).
실행: ``python -m trading.factpack [top_n]``.
"""

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from trading.collectors.base import KST, now_kst
from trading.collectors.dart import DartClient
from trading.collectors.market import MarketStore
from trading.contracts.factpack import DisclosureItem, FactPack, FinancialLine, PriceContext
from trading.screener import Candidate, SECTOR_SOURCES, ScreenConfig, screen

DART_DISCLOSURE_DAYS = 90
DISCLOSURE_LIMIT = 15
# 재무 기간 폴백(최신→과거). DART는 미제출 기간에 status 013 → 빈 → 다음 후보로.
FIN_REPORTS = ("11014", "11013", "11012", "11011")  # 3Q·1Q·반기·사업

# 정규화 라벨 → account_nm 별칭(부분일치). 연결(CFS) 우선.
KEY_ACCOUNTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("매출액", ("매출액", "영업수익")),
    ("영업이익", ("영업이익",)),
    ("당기순이익", ("당기순이익",)),
    ("자산총계", ("자산총계",)),
    ("부채총계", ("부채총계",)),
    ("자본총계", ("자본총계",)),
)


class DartLike(Protocol):
    """build_fact_pack 이 필요로 하는 DART 표면(테스트·NullDart 주입용)."""

    def disclosures(self, corp_code: str, bgn_de: str, end_de: str) -> list[dict[str, object]]: ...
    def financials(self, corp_code: str, bsns_year: str, reprt_code: str) -> list[dict[str, object]]: ...


def _parse_amount(raw: object) -> float | None:
    """DART 금액 문자열(콤마·괄호음수) → float. 빈/비수치 → None(추측 금지)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in {"-", "—"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace(" ", "")
    if neg and not s.startswith("-"):
        s = "-" + s
    try:
        return float(s)
    except ValueError:
        return None


def _fin_periods(as_of_year: int) -> list[tuple[str, str]]:
    """as_of 연도 기준 (bsns_year, reprt_code) 폴백 — 올해·작년만(과추측 방지)."""
    out: list[tuple[str, str]] = []
    for yr in (as_of_year, as_of_year - 1):
        out.extend((str(yr), rc) for rc in FIN_REPORTS)
    return out


def _extract_financials(rows: list[dict[str, object]]) -> list[FinancialLine]:
    """주요계정 rows → 핵심 라인(연결 우선, 당기/전기 + YoY)."""
    out: list[FinancialLine] = []
    for label, aliases in KEY_ACCOUNTS:
        cand = [r for r in rows if any(a in str(r.get("account_nm", "")) for a in aliases)]
        if not cand:
            continue
        pick = next((r for r in cand if str(r.get("fs_div")) == "CFS"), cand[0])
        ths = _parse_amount(pick.get("thstrm_amount"))
        frm = _parse_amount(pick.get("frmtrm_amount"))
        yoy = (ths - frm) / abs(frm) * 100 if (ths is not None and frm is not None and frm != 0) else None
        out.append(
            FinancialLine(
                account=label,
                fs_div=str(pick.get("fs_div")) if pick.get("fs_div") else None,
                thstrm=ths,
                frmtrm=frm,
                yoy_pct=round(yoy, 1) if yoy is not None else None,
            )
        )
    return out


def _price_context(c: Candidate, store: MarketStore) -> PriceContext:
    g = c.signals
    q = store.latest_quote(c.srtn_cd)
    return PriceContext(
        as_of=q[0] if q else "",
        market=c.market,
        close=c.clpr,
        market_cap=_parse_amount(q[3]) if q else None,
        tr_value_surge=round(g.tr_value_surge, 2),
        mom_short_pct=round(g.mom_short * 100, 1),
        mom_long_pct=round(g.mom_long * 100, 1),
        high_252_proximity=round(g.high_proximity, 3),
    )


def _shift_days(yyyymmdd: str, days: int) -> str:
    return (datetime.strptime(yyyymmdd, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def _as_of_aware(yyyymmdd: str) -> datetime | None:
    if not re.fullmatch(r"\d{8}", yyyymmdd or ""):
        return None
    return datetime.strptime(yyyymmdd, "%Y%m%d").replace(hour=15, minute=30, tzinfo=KST)


def build_fact_pack(
    c: Candidate,
    store: MarketStore,
    dart: DartLike,
    corp_map: dict[str, tuple[str, str]],
    sectors: list[str],
) -> FactPack:
    """후보 1종목의 결정론 FactPack 조립. DART 결측은 notes에 기록(추측 금지)."""
    now = now_kst()
    price = _price_context(c, store)
    notes: list[str] = []
    sources: dict[str, str] = {"price": "data.go.kr:전종목시세(DB)"}
    disclosures: list[DisclosureItem] = []
    financials: list[FinancialLine] = []
    fin_period: str | None = None
    fallback_date = price.as_of or now.strftime("%Y%m%d")

    ent = corp_map.get(c.srtn_cd)
    if ent is None or not ent[0]:
        notes.append("DART corp_code 없음 — 공시·재무 미수집")
    else:
        corp_code = ent[0]
        sources["disclosure"] = f"DART:list.json/{corp_code}"
        sources["financials"] = f"DART:fnlttSinglAcnt.json/{corp_code}"
        end = fallback_date
        bgn = _shift_days(end, -DART_DISCLOSURE_DAYS)
        for d in dart.disclosures(corp_code, bgn, end)[:DISCLOSURE_LIMIT]:
            rcept_no, report_nm, rcept_dt = d.get("rcept_no"), d.get("report_nm"), d.get("rcept_dt")
            if rcept_no and report_nm and rcept_dt:
                disclosures.append(
                    DisclosureItem(
                        rcept_dt=str(rcept_dt),
                        report_nm=str(report_nm),
                        rcept_no=str(rcept_no),
                        flr_nm=str(d["flr_nm"]) if d.get("flr_nm") else None,
                    )
                )
        if not disclosures:
            notes.append(f"공시 없음({bgn}~{end})")

        for yr, rc in _fin_periods(int(fallback_date[:4])):
            rows = dart.financials(corp_code, yr, rc)
            if rows:
                financials = _extract_financials(rows)
                fin_period = f"{yr}/{rc}"
                break
        if not financials:
            notes.append("재무 주요계정 미수집(최근 보고서 없음)")

    return FactPack(
        srtn_cd=c.srtn_cd,
        name=c.name,
        sectors=sectors,
        screen_score=round(c.score, 4),
        price=price,
        disclosures=disclosures,
        fin_period=fin_period,
        financials=financials,
        sources=sources,
        notes=notes,
        as_of=_as_of_aware(price.as_of) or now,
        fetched_at=now,
    )


class _NullDart:
    """DART 키 없을 때 — 공시·재무를 빈 값으로(추측 대체 금지)."""

    def disclosures(self, corp_code: str, bgn_de: str, end_de: str) -> list[dict[str, object]]:
        return []

    def financials(self, corp_code: str, bsns_year: str, reprt_code: str) -> list[dict[str, object]]:
        return []


@dataclass(frozen=True)
class FactPackRun:
    as_of: str
    written: int
    out_dir: str


def _safe_name(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "_", name).strip("_") or "x"


def _fmt_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}" if len(yyyymmdd) == 8 else yyyymmdd


def run(top_n: int = 15) -> FactPackRun:
    key = os.environ.get("DART_API_KEY", "")
    store = MarketStore()
    res = screen(store, ScreenConfig(top_n=top_n))
    if not res.candidates:
        store.close()
        return FactPackRun("", 0, "")
    secmap = store.sector_map_multi(SECTOR_SOURCES)
    dart: DartLike = DartClient(key) if key else _NullDart()
    corp_map = dart.corp_code_map() if isinstance(dart, DartClient) else {}
    out_dir = Path(".runtime") / "factpack" / _fmt_date(res.as_of)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for c in res.candidates:
        pack = build_fact_pack(c, store, dart, corp_map, secmap.get(c.srtn_cd, []))
        path = out_dir / f"{c.srtn_cd}_{_safe_name(c.name)}.json"
        path.write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        written += 1
    store.close()
    return FactPackRun(res.as_of, written, str(out_dir))


def main() -> int:
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    r = run(top_n)
    if r.written == 0:
        print("FactPack 미생성 (후보 없음 — DB/스크리너 확인)")
        return 0
    blocked = "" if os.environ.get("DART_API_KEY") else " · ⚠️ DART 키 없음(공시·재무 blocked)"
    print(f"FactPack as_of={r.as_of}: {r.written}건 → {r.out_dir}{blocked}")
    return 0


__all__ = ["DartLike", "FactPackRun", "build_fact_pack", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
