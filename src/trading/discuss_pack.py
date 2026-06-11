"""DiscussPack 조립기 — `/discuss` 토론 컨텍스트 사전 조립 + 캐싱 (PROPOSALS P-5).

**결정론 조립**(LLM 미개입) + 뉴스 사실검증만 기존 R2→R4 경로(claude -p) 재사용:
1. 종목 해석(코드/이름) → 뉴스 보강(부족 시 종목 쿼리 수집 내장 — COLLECT-4 범위)
   → 수급 보강(없거나 뒤처지면 KIS 종목별 1콜) → FactPack 조립(가격·DART·뉴스·수급).
2. 단기 변동률(5거래일) + 투자자별 누적 포지션(5/20일, 연기금 분리).
3. 이벤트: 기검증분(EventStore) 재사용, **미평가 뉴스만** scoped R2(이벤트화)→R4(3렌즈
   적대검증). 종목 직접 이벤트 + 소속 섹터(sector_theme) 이벤트 포함(운영자 결정 ③).
4. `data/discuss.sqlite` append-only 새 버전 저장. 갱신은 자동이 아니라 운영자 확인
   (스킬이 `--check` 출력을 보고 질문 — 운영자 결정).

CLI:
  python -m trading.discuss_pack <코드|이름> --check          # 캐시·신선도(결정론 판정)
  python -m trading.discuss_pack <코드|이름> --build [--no-verify]
  python -m trading.discuss_pack <코드|이름>                  # 캐시 최신 버전 JSON 출력
"""

import re
import sys

from trading.collectors.base import CollectError, now_kst
from trading.collectors.flows import FlowStore, collect_stock
from trading.collectors.kis import client_from_env as kis_from_env
from trading.collectors.market import MarketStore
from trading.collectors.news import NewsStore, build_query_plan, collect_news
from trading.contracts.discuss import DiscussPack, EventBrief, FlowCumulative
from trading.contracts.event import EventRecord, Scope
from trading.journal.discuss import DiscussStore
from trading.journal.events import EventStore

MIN_NEWS = 3          # 미만이면 종목 쿼리 수집 내장(운영자 결정 ①)
NEWS_FETCH_LIMIT = 10
CUM_WINDOWS = (5, 20)
EVENT_SCAN = 300      # 이벤트 텍스트 매칭·섹터 매칭 스캔 폭
R4_DISCUSS_MAX = 10   # 토론 전수검증 비용 상한

# 통용 한글명 ↔ DB 등록명(영문 등) 별칭 — 운영하며 추가. 추측 매핑 금지(확인분만).
ALIASES: dict[str, str] = {
    "네이버": "NAVER",
}


def _resolve(store: MarketStore, ident: str) -> tuple[str, str] | None:
    """코드(6자리) 또는 이름(별칭 포함) → (srtn_cd, name). DB 미수집이면 None."""
    latest = store.latest_date()
    if latest is None:
        return None
    if ident.isdigit() and len(ident) == 6:
        recs = store.series_for(ident, latest)
        return (ident, str(recs[-1][1])) if recs else None
    for q in (ident, ALIASES.get(ident, "")):
        if not q:
            continue
        matches = store.find_by_name(q)
        if matches:
            return (matches[0][0], matches[0][1])
    return None


def _name_terms(code: str, name: str) -> list[str]:
    """종목 텍스트 매칭 용어(소문자) — DB명 + 역방향 별칭."""
    terms = [name.lower()]
    terms.extend(k.lower() for k, v in ALIASES.items() if v.lower() == name.lower())
    terms.append(code)
    return terms


# 이메일·URL 보일러플레이트 — 'kocykim@naver.com'·'blog.naver.com' 류가 종목 매칭을
# 오염시킨다(2026-06-11 네이버 잡음 기사 관측). 매칭 전 제거.
_MATCH_NOISE = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"                      # 이메일
    r"|https?://\S+"                                       # URL
    r"|[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|kr)\b\S*"      # 도메인(blog.naver.com 등)
)


def _clean_for_match(text: str) -> str:
    return _MATCH_NOISE.sub(" ", text).lower()


def _enrich_news(code: str, name: str, notes: list[str]) -> None:
    """종목 뉴스가 빈약하면 종목 쿼리 수집(내장). 소스 키 없으면 결측 기록만."""
    from trading.collect_news import build_sources_from_env

    nstore = NewsStore()
    try:
        have = nstore.recent_for([code], limit=MIN_NEWS)
        if len(have) >= MIN_NEWS:
            return
        sources = build_sources_from_env()
        if not sources:
            notes.append("뉴스 빈약 + 소스 키 없음(NAVER/SEARXNG) — 보강 불가")
            return
        plan = build_query_plan([(code, name)], sectors=(), themes=())
        summary = collect_news(sources, plan, nstore, limit=NEWS_FETCH_LIMIT)
        notes.append(f"뉴스 보강 수집: 적재 {summary.stored}건 (blocked {len(summary.blocked)})")
    finally:
        nstore.close()


def _enrich_flows(code: str, name: str, latest_bas_dt: str, notes: list[str]) -> None:
    """수급이 없거나 시세 최신일보다 뒤처지면 KIS 종목별 1콜 수집. 키 없으면 기록만."""
    fstore = FlowStore()
    try:
        rows = fstore.recent_for("stock", code, limit=1)
        if rows and rows[0][0] >= latest_bas_dt:
            return
        kis = kis_from_env()
        if kis is None:
            notes.append("수급 미수집 + KIS 키 없음 — 보강 불가")
            return
        try:
            n = collect_stock(kis, fstore, code, name, latest_bas_dt)
            notes.append(f"수급 보강 수집: 신규 {n}행(KIS ≈30거래일)")
        except CollectError as exc:
            notes.append(f"수급 보강 실패: {exc}")
    finally:
        fstore.close()


def _price_chg_5d(store: MarketStore, code: str) -> float | None:
    """최근 5거래일 변동률(%) — 데이터 6일 미만이면 None(추측 금지)."""
    cutoff = store.nth_recent_date(6)
    if cutoff is None:
        return None
    closes = store.closes_for(code, cutoff)
    if len(closes) < 6:
        return None
    base, last = closes[-6][1], closes[-1][1]
    if base == 0:
        return None
    return round((last / base - 1.0) * 100.0, 2)


def _f(v: str | None) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _flows_cumulative(code: str, store: FlowStore | None = None) -> list[FlowCumulative]:
    """최근 5/20거래일 투자자별 누적 순매수(백만원, 연기금 분리). 비수치 행은 제외."""
    own = store is None
    fstore = store if store is not None else FlowStore()
    try:
        rows = fstore.recent_for("stock", code, limit=max(CUM_WINDOWS))
    finally:
        if own:
            fstore.close()
    out: list[FlowCumulative] = []
    for days in CUM_WINDOWS:
        window = rows[:days]
        if not window:
            out.append(FlowCumulative(days=days, days_counted=0, prsn_mn=None, frgn_mn=None,
                                      fund_mn=None, orgn_ex_fund_mn=None))
            continue
        sums = {"prsn": 0.0, "frgn": 0.0, "fund": 0.0, "orgn_ex": 0.0}
        counted = {k: 0 for k in sums}
        for _bas, prsn, frgn, orgn, fund in window:
            p, f_, o, fu = _f(prsn), _f(frgn), _f(orgn), _f(fund)
            if p is not None:
                sums["prsn"] += p
                counted["prsn"] += 1
            if f_ is not None:
                sums["frgn"] += f_
                counted["frgn"] += 1
            if fu is not None:
                sums["fund"] += fu
                counted["fund"] += 1
            if o is not None and fu is not None:
                sums["orgn_ex"] += o - fu
                counted["orgn_ex"] += 1
        out.append(
            FlowCumulative(
                days=days,
                days_counted=len(window),
                prsn_mn=round(sums["prsn"], 1) if counted["prsn"] else None,
                frgn_mn=round(sums["frgn"], 1) if counted["frgn"] else None,
                fund_mn=round(sums["fund"], 1) if counted["fund"] else None,
                orgn_ex_fund_mn=round(sums["orgn_ex"], 1) if counted["orgn_ex"] else None,
            )
        )
    return out


def _brief(e: EventRecord) -> EventBrief:
    v = e.verification
    status = "unverified" if v is None else ("confirmed" if v.confirmed else "refuted")
    lenses: str | None = None
    lens_notes: list[str] = []
    if v is not None:
        survived = sum(1 for lv in v.lens_verdicts if lv.survived)
        lenses = f"{survived}/{len(v.lens_verdicts)}"
        lens_notes = [
            f"[{lv.lens}{'·생존' if lv.survived else '·기각'}] {lv.reason[:120]}"
            for lv in v.lens_verdicts
        ]
    return EventBrief(
        id=e.id,
        summary_1line=e.summary_1line,
        catalyst_type=e.catalyst_type.value if e.catalyst_type else None,
        scope=e.scope.value if e.scope else None,
        strength=e.catalyst_strength,
        status=status,
        verified_by=v.verified_by if v else None,
        lenses=lenses,
        lens_notes=lens_notes,
        as_of=e.as_of,
    )


def _stock_events(es: EventStore, code: str, name: str) -> list[EventRecord]:
    """종목 연결 이벤트 — affected 조인 + **요약/entities 텍스트 매칭** 보조 스캔.

    R2 affected는 당시 후보 universe로 제한돼(환각가드) 비후보 종목은 affected에 빠질
    수 있다(2026-06-11 네이버 사례: 요약에 '네이버'가 있어도 affected 미포함) — 그래서
    텍스트 매칭이 필수다. id 중복은 제거.
    """
    terms = _name_terms(code, name)
    by_id: dict[str, EventRecord] = {e.id: e for e in es.for_srtn(code, limit=50)}
    for e in es.recent(limit=EVENT_SCAN):
        if e.id in by_id:
            continue
        text = e.summary_1line.lower()
        ents = {x.lower() for x in e.entities}
        if any(t in text for t in terms) or any(t in ents for t in terms):
            by_id[e.id] = e
    return sorted(by_id.values(), key=lambda e: e.as_of, reverse=True)


def _verify_events(
    code: str, name: str, news_ids_in_pack: list[str], notes: list[str],
    ds: DiscussStore,
) -> list[EventBrief]:
    """기검증 이벤트 재사용 + 미평가 뉴스만 R2 → 종목 관련 미검증 이벤트 **전수** R4.

    일별 파이프라인의 R4 선별 임계(비용 가드)는 토론 맥락에 부적합(운영자 2026-06-11)
    — 토론 종목 이벤트는 scope·강도 무관 전수 검증(상한 R4_DISCUSS_MAX).
    """
    from trading.gates.news import gate_news
    from trading.llm import client_from_env as llm_from_env
    from trading.rounds.r2 import R2Config, run_r2
    from trading.rounds.r4 import FACT_LENSES, EventProgress, R4Config, run_r4

    es = EventStore()
    try:
        events = _stock_events(es, code, name)
        covered = {eid for e in events for eid in e.evidence}
        covered |= ds.processed_news_ids(code)  # 미이벤트화 뉴스 재처리 방지(원장)
        new_ids = [nid for nid in news_ids_in_pack if nid not in covered]
        llm = None
        if new_ids:
            nstore = NewsStore()
            try:
                new_items = nstore.by_ids(new_ids)
            finally:
                nstore.close()
            if new_items:
                llm = llm_from_env()
                r2 = run_r2(llm, gate_news(new_items), [(code, name)], config=R2Config())
                if r2.events:
                    es.append(r2.events)
                ds.mark_news_processed(code, [n.id for n in new_items], now_kst().isoformat())
                notes.append(
                    f"뉴스 검증(R2): 신규 {len(new_items)}건 → 이벤트 {len(r2.events)} "
                    f"(폐기 {r2.rejected}, LLM에러 {len(r2.batch_errors)})"
                )
                events = _stock_events(es, code, name)
        unverified = [e for e in events if e.verification is None][:R4_DISCUSS_MAX]
        if unverified:
            need = sorted({eid for e in unverified for eid in e.evidence})
            nstore2 = NewsStore()
            try:
                evidence = {n.id: n for n in nstore2.by_ids(need)}
            finally:
                nstore2.close()

            def _on_event(p: EventProgress) -> None:
                es.append([p.event])

            # 토론 전수검증 — 선별 임계 해제(0.0), 상한만 유지.
            # 렌즈는 **사실성(가짜뉴스)** 기준(운영자: 검증 목표=사실 여부, 중요도 판단 금지).
            discuss_cfg = R4Config(
                strength_threshold=0.0, high_strength=0.0, max_events=R4_DISCUSS_MAX
            )
            r4 = run_r4(llm or llm_from_env(), unverified, evidence,
                        config=discuss_cfg, source="r4:fact-check",
                        on_event=_on_event, lenses=FACT_LENSES)
            notes.append(
                f"사실성 검증(R4 fact-check): 대상 {len(unverified)} / 검증 {len(r4.verified)}"
            )
            events = _stock_events(es, code, name)
        # 소속 섹터 이벤트(운영자 결정 ③ — 종목 + 섹터까지, broad_market 제외)
        mstore = MarketStore()
        try:
            from trading.screener import SECTOR_SOURCES

            sectors = set(mstore.sector_map_multi(SECTOR_SOURCES).get(code, []))
        finally:
            mstore.close()
        stock_ids = {e.id for e in events}
        sector_events = [
            e for e in es.recent(limit=EVENT_SCAN)
            if e.scope is Scope.SECTOR_THEME and e.id not in stock_ids
            and (set(e.entities) & sectors)
        ]
        return [_brief(e) for e in events] + [_brief(e) for e in sector_events]
    finally:
        es.close()


def build(ident: str, *, verify: bool = True) -> tuple[int, DiscussPack] | None:
    """팩 조립 → 캐시 새 버전 저장. (version, pack) 반환. 종목 미해석이면 None."""
    from trading.factpack import build_fact_pack_for

    notes: list[str] = []
    mstore = MarketStore()
    try:
        resolved = _resolve(mstore, ident)
        if resolved is None:
            return None
        code, name = resolved
        latest = mstore.latest_date() or ""
        _enrich_news(code, name, notes)
        _enrich_flows(code, name, latest, notes)
        fact = build_fact_pack_for(code, store=mstore)
        if fact is None:
            return None
        # 뉴스 정합 필터 — **무관(엔티티 불일치) 기사만** 제외. 중요도·신선도로 거르지
        # 않는다(운영자: "경제 관련 필요없는 뉴스는 없다"). 이메일·URL 보일러플레이트
        # (kocykim@naver.com 류)는 매칭에서 제거해 가짜 일치를 막는다.
        terms = _name_terms(code, name)
        relevant = [
            n for n in fact.news
            if any(t in _clean_for_match(f"{n.title} {n.snippet or ''}") for t in terms)
        ]
        if len(relevant) != len(fact.news):
            notes.append(
                f"뉴스 정합 필터(엔티티 불일치만 제외): {len(fact.news)}건 → 채택 {len(relevant)}"
            )
            fact = fact.model_copy(update={"news": relevant})
        chg5 = _price_chg_5d(mstore, code)
    finally:
        mstore.close()

    ds = DiscussStore()
    try:
        if verify:
            events = _verify_events(code, name, [n.id for n in fact.news], notes, ds)
        else:
            es = EventStore()
            try:
                events = [_brief(e) for e in _stock_events(es, code, name)]
            finally:
                es.close()
            notes.append("검증 생략(--no-verify) — 기존 이벤트만 포함")

        pack = DiscussPack(
            fact=fact, price_chg_5d_pct=chg5, flows_cum=_flows_cumulative(code),
            events=events, built_at=now_kst(), notes=notes,
        )
        version = ds.append(pack)
    finally:
        ds.close()
    return version, pack


def check(ident: str) -> int:
    """캐시 유무·신선도 출력(결정론). 스킬이 '갱신할까?' 질문에 사용."""
    mstore = MarketStore()
    try:
        resolved = _resolve(mstore, ident)
        if resolved is None:
            print(f"종목 못 찾음(DB 미수집?): {ident} — /collect 후 재시도")
            return 1
        code, name = resolved
        latest = mstore.latest_date() or "(없음)"
    finally:
        mstore.close()
    print(f"종목: {code} {name}")
    print(f"시세 DB 최신 거래일: {latest}")
    ds = DiscussStore()
    try:
        cached = ds.latest(code)
    finally:
        ds.close()
    if cached is None:
        print("캐시: 없음 → --build 필요")
        return 0
    version, pack = cached
    fresh = "FRESH" if pack.fact.price.as_of >= latest else f"STALE(시세 {latest}) — 갱신 권장"
    print(
        f"캐시: v{version} · built {pack.built_at.isoformat(timespec='minutes')} · "
        f"price_as_of {pack.fact.price.as_of} · {fresh}"
    )
    print(f"  뉴스 {len(pack.fact.news)}건 · 이벤트 {len(pack.events)}건 · "
          f"수급 일별 {len(pack.fact.flows)}건")
    return 0


def _print_build_summary(version: int, pack: DiscussPack) -> None:
    f = pack.fact
    print(f"DiscussPack v{version} 저장: {f.srtn_cd} {f.name} "
          f"(price_as_of {f.price.as_of}, built {pack.built_at.isoformat(timespec='minutes')})")
    print(f"  섹터: {', '.join(f.sectors) or '미분류'} · 5일 변동 "
          f"{pack.price_chg_5d_pct if pack.price_chg_5d_pct is not None else '?'}%")
    for c in pack.flows_cum:
        def aek(v: float | None) -> str:
            return f"{v / 100:+,.0f}" if v is not None else "?"
        print(f"  수급 {c.days}일 누적(억원, {c.days_counted}일분): 개인 {aek(c.prsn_mn)} | "
              f"외국인 {aek(c.frgn_mn)} | 연기금 {aek(c.fund_mn)} | 기관外 {aek(c.orgn_ex_fund_mn)}")
    by_status: dict[str, int] = {}
    for e in pack.events:
        by_status[e.status] = by_status.get(e.status, 0) + 1
    print(f"  이벤트: {len(pack.events)}건 ({by_status or '없음'}) · 뉴스 {len(f.news)}건 · "
          f"공시 {len(f.disclosures)}건")
    for n in pack.notes:
        print(f"  note: {n}")


def main() -> int:
    args = [a for a in sys.argv[1:]]
    flags = {a for a in args if a.startswith("--")}
    idents = [a for a in args if not a.startswith("--")]
    if not idents:
        print("usage: python -m trading.discuss_pack <코드|이름> [--check|--build] [--no-verify]")
        return 2
    ident = idents[0]
    if "--check" in flags:
        return check(ident)
    if "--build" in flags:
        result = build(ident, verify="--no-verify" not in flags)
        if result is None:
            print(f"종목 못 찾음(DB 미수집?): {ident} — /collect 후 재시도")
            return 1
        _print_build_summary(*result)
        return 0
    # 기본: 캐시 최신 버전 JSON(토론 grounding 입력)
    mstore = MarketStore()
    try:
        resolved = _resolve(mstore, ident)
    finally:
        mstore.close()
    if resolved is None:
        print(f"종목 못 찾음(DB 미수집?): {ident}")
        return 1
    ds = DiscussStore()
    try:
        cached = ds.latest(resolved[0])
    finally:
        ds.close()
    if cached is None:
        print(f"캐시 없음: {resolved[0]} {resolved[1]} — 먼저 --build")
        return 1
    print(cached[1].model_dump_json(indent=2))
    return 0


__all__ = ["build", "check", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
