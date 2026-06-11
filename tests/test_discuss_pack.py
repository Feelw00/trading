"""DiscussPack(P-5) — 계약 왕복·캐시 버전·누적 수급·이벤트 요약·신선도 판정."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.collectors.flows import FlowStore
from trading.contracts.discuss import DiscussPack, EventBrief, FlowCumulative
from trading.contracts.event import EventRecord, EventType, Scope, Verification
from trading.contracts.factpack import FactPack, PriceContext
from trading.discuss_pack import _brief, _flows_cumulative
from trading.journal.discuss import DiscussStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 11, 14, 0, tzinfo=KST)


def _fact(as_of: str = "20260610") -> FactPack:
    return FactPack(
        srtn_cd="093370", name="후성", sectors=["chemicals"], screen_score=0.0,
        price=PriceContext(as_of=as_of, market="KOSPI", close=10000.0, market_cap=1e12,
                           tr_value_surge=1.0, mom_short_pct=0.0, mom_long_pct=0.0,
                           high_252_proximity=0.9),
        as_of=NOW, fetched_at=NOW,
    )


def _pack(as_of: str = "20260610") -> DiscussPack:
    return DiscussPack(
        fact=_fact(as_of),
        price_chg_5d_pct=1.5,
        flows_cum=[FlowCumulative(days=5, days_counted=5, prsn_mn=-100.0, frgn_mn=50.0,
                                  fund_mn=10.0, orgn_ex_fund_mn=40.0)],
        events=[EventBrief(id="ev-1", summary_1line="테스트 이벤트", status="confirmed",
                           as_of=NOW)],
        built_at=NOW,
        notes=["테스트"],
    )


def test_discuss_pack_json_roundtrip() -> None:
    pack = _pack()
    again = DiscussPack.model_validate_json(pack.model_dump_json())
    assert again == pack
    assert again.flows_cum[0].orgn_ex_fund_mn == 40.0


def test_discuss_store_versioning(tmp_path: Path) -> None:
    store = DiscussStore(tmp_path / "discuss.sqlite")
    assert store.latest("093370") is None
    assert store.append(_pack("20260609")) == 1
    assert store.append(_pack("20260610")) == 2  # 갱신 = 새 버전(append-only)
    latest = store.latest("093370")
    assert latest is not None
    version, pack = latest
    assert version == 2 and pack.fact.price.as_of == "20260610"
    assert store.versions("093370") == 2
    store.close()


def test_flows_cumulative_windows_and_fund_split(tmp_path: Path) -> None:
    fstore = FlowStore(tmp_path / "flows.sqlite")
    rows = [
        {"stck_bsop_date": f"202606{d:02d}", "prsn_ntby_tr_pbmn": "100",
         "frgn_ntby_tr_pbmn": "-50", "orgn_ntby_tr_pbmn": "30", "fund_ntby_tr_pbmn": "10"}
        for d in range(1, 9)  # 8거래일
    ]
    fstore.upsert("stock", "093370", "후성", rows)
    cum5, cum20 = _flows_cumulative("093370", fstore)
    assert cum5.days == 5 and cum5.days_counted == 5
    assert cum5.prsn_mn == 500.0 and cum5.frgn_mn == -250.0
    assert cum5.fund_mn == 50.0 and cum5.orgn_ex_fund_mn == 100.0  # (30-10)×5
    assert cum20.days == 20 and cum20.days_counted == 8  # 부분합 명시
    fstore.close()


def test_flows_cumulative_empty_is_none(tmp_path: Path) -> None:
    fstore = FlowStore(tmp_path / "flows.sqlite")
    cum5, _ = _flows_cumulative("000000", fstore)
    assert cum5.days_counted == 0 and cum5.prsn_mn is None
    fstore.close()


def test_stock_events_text_match_catches_missing_affected(tmp_path: Path) -> None:
    """affected에 코드가 빠져도(비후보 시절 R2 산출) 요약 텍스트로 잡아야 한다(네이버 사례)."""
    from trading.journal.events import EventStore

    from trading.contracts.event import AffectedStock

    es = EventStore(tmp_path / "events.sqlite")

    def _ev(eid: str, summary: str, *, affected: list[AffectedStock] | None = None,
            entities: list[str] | None = None) -> EventRecord:
        return EventRecord(
            id=eid, as_of=NOW, fetched_at=NOW, source="test", type=EventType.EARNINGS,
            summary_1line=summary, affected=affected or [], entities=entities or [],
        )

    es.append([
        _ev("ev-affected", "후성 직접 이벤트",
            affected=[AffectedStock(srtn_cd="093370", relevance=0.5)]),
        _ev("ev-text", "엔비디아가 NAVER 등과 협력 발표", entities=["theme:x"]),
        _ev("ev-unrelated", "무관 이벤트"),
    ])
    from trading.discuss_pack import _stock_events

    naver = _stock_events(es, "035420", "NAVER")
    assert [e.id for e in naver] == ["ev-text"]  # 텍스트 매칭(소문자 무관)
    husung = _stock_events(es, "093370", "후성")
    assert {e.id for e in husung} == {"ev-affected"}  # affected 조인 + 무관 제외
    es.close()


def test_resolve_alias(tmp_path: Path) -> None:
    from trading.collectors.market import MarketStore
    from trading.discuss_pack import _name_terms, _resolve

    store = MarketStore(tmp_path / "m.sqlite")
    store.upsert([{"basDt": "20260610", "srtnCd": "035420", "itmsNm": "NAVER",
                   "mrktCtg": "KOSPI", "clpr": "227000"}])
    assert _resolve(store, "네이버") == ("035420", "NAVER")   # 별칭 → DB명
    assert _resolve(store, "NAVER") == ("035420", "NAVER")
    assert _resolve(store, "035420") == ("035420", "NAVER")
    assert _resolve(store, "없는종목") is None
    assert "네이버" in _name_terms("035420", "NAVER")          # 역방향 별칭
    store.close()


def test_brief_status_mapping() -> None:
    base = dict(
        id="ev-1", as_of=NOW, fetched_at=NOW, source="test",
        type=EventType.EARNINGS, summary_1line="요약", scope=Scope.SINGLE_STOCK,
    )
    from trading.contracts.event import LensVerdict

    unverified = EventRecord(**base)  # type: ignore[arg-type]
    b = _brief(unverified)
    assert b.status == "unverified" and b.lenses is None and b.lens_notes == []
    verdicts = [
        LensVerdict(lens="strength", survived=False, reason="이미 가격 반영"),
        LensVerdict(lens="linkage", survived=True, reason="종목 직접 연결"),
        LensVerdict(lens="timing", survived=False, reason="과거 이벤트"),
    ]
    refuted = unverified.model_copy(
        update={"verification": Verification(
            verified_by="r4:test", confirmed=False, lens_verdicts=verdicts)}
    )
    rb = _brief(refuted)
    assert rb.status == "refuted" and rb.lenses == "1/3"  # 기각이어도 생존비 노출(의견 가중 제한)
    assert any("linkage·생존" in n for n in rb.lens_notes)
    assert any("timing·기각" in n for n in rb.lens_notes)
    confirmed = unverified.model_copy(
        update={"verification": Verification(verified_by="r4:test", confirmed=True)}
    )
    assert _brief(confirmed).status == "confirmed"
    assert _brief(confirmed).scope == "single_stock"
