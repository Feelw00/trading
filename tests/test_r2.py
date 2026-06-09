"""R2 촉매 스코어러 — 배치·파싱·환각가드·스키마 폐기(LLM 주입, 프로세스 없음)."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from trading.contracts.news import NewsItem
from trading.gates.news import NewsFlag, NewsVerdict
from trading.llm import LLMError
from trading.rounds.r2 import R2Config, build_batches, run_r2

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 9, 16, 0, tzinfo=KST)
CANDS = [("001740", "SK네트웍스"), ("005930", "삼성전자")]


def _news(nid: str, entities: list[str], *, published_at: datetime | None = NOW, trust: float = 0.9) -> NewsItem:
    return NewsItem(
        id=nid, source="naver", query="q", title=f"기사 {nid}", url=f"https://yna.co.kr/{nid}",
        publisher="연합뉴스", published_at=published_at, fetched_at=NOW, trust=trust, entities=entities,
    )


def _fresh(it: NewsItem) -> NewsVerdict:
    return NewsVerdict(item=it, flags=frozenset())


class _FakeClient:
    def __init__(self, by_key: dict[str, str] | None = None, default: str = '{"events": []}',
                 error_keys: set[str] | None = None) -> None:
        self.by_key = by_key or {}
        self.default = default
        self.error_keys = error_keys or set()
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        for key, resp in self.by_key.items():
            if f"## 배치 키\n{key}" in prompt:
                if key in self.error_keys:
                    raise LLMError("boom")
                return resp
        return self.default


def test_build_batches_groups_by_primary_key() -> None:
    items = [
        _news("a", ["001740"]),
        _news("b", ["001740", "sector:retail_consumer"]),  # 종목 우선
        _news("c", ["sector:semiconductor"]),
        _news("d", ["theme:fed"]),
        _news("e", []),                                     # 미분류 → 스킵
    ]
    batches = build_batches(items, R2Config())
    assert set(batches) == {"001740", "sector:semiconductor", "theme:fed"}
    assert {it.id for it in batches["001740"]} == {"a", "b"}


def test_build_batches_caps_items() -> None:
    items = [_news(str(i), ["001740"], published_at=NOW - timedelta(hours=i)) for i in range(20)]
    batches = build_batches(items, R2Config(max_items_per_batch=5))
    assert len(batches["001740"]) == 5
    # 최신순 정렬 — 가장 최근(i=0)이 포함
    assert "0" in {it.id for it in batches["001740"]}


_GOOD = (
    '{"events":[{"event_type":"corp_action","catalyst_type":"supply_chain","scope":"single_stock",'
    '"catalyst_strength":0.7,"novelty":0.6,"summary_1line":"SK네트웍스 AI 데이터센터 수주",'
    '"affected":[{"srtn_cd":"001740","relevance":0.9}],"evidence":["n1","n2","ghost"]}]}'
)


def test_run_r2_constructs_event_record() -> None:
    items = [_news("n1", ["001740"], published_at=NOW - timedelta(hours=5)),
             _news("n2", ["001740"], published_at=NOW - timedelta(hours=2))]
    res = run_r2(_FakeClient(by_key={"001740": _GOOD}), [_fresh(i) for i in items], CANDS, now=NOW)
    assert res.batches == 1 and res.rejected == 0
    assert len(res.events) == 1
    ev = res.events[0]
    assert ev.type.value == "corp_action"
    assert ev.catalyst_type is not None and ev.catalyst_type.value == "supply_chain"
    assert ev.scope is not None and ev.scope.value == "single_stock"
    assert ev.catalyst_strength == 0.7 and ev.novelty == 0.6
    assert ev.affected[0].srtn_cd == "001740" and ev.affected[0].relevance == 0.9
    assert "001740" in ev.entities
    # 환각가드: 배치에 없는 'ghost' 는 evidence 에서 탈락
    assert ev.evidence == ["n1", "n2"]
    # as_of = evidence 중 최신 발행일
    assert ev.as_of == NOW - timedelta(hours=2)


def test_run_r2_coerces_bad_optional_fields() -> None:
    # 부적합 catalyst_type/scope·범위밖 score는 폐기 아님 → None으로 관대 처리(이벤트 유지)
    lenient = '{"events":[{"event_type":"corp_action","catalyst_type":"NONSENSE","scope":"WRONG",' \
              '"catalyst_strength":1.8,"novelty":-0.2,"summary_1line":"실데이터 이벤트",' \
              '"affected":[],"evidence":[]}]}'
    res = run_r2(_FakeClient(by_key={"001740": lenient}), [_fresh(_news("n1", ["001740"]))], CANDS, now=NOW)
    assert res.rejected == 0 and len(res.events) == 1
    ev = res.events[0]
    assert ev.catalyst_type is None and ev.scope is None
    assert ev.catalyst_strength is None and ev.novelty is None  # 추측·클램프 안 함


def test_run_r2_coerces_event_type_from_catalyst() -> None:
    # event_type가 enum 밖("macro")이면 catalyst_type 맵으로 보정(macro→geopolitics)
    j = '{"events":[{"event_type":"macro","catalyst_type":"macro","scope":"broad_market",' \
        '"catalyst_strength":0.5,"summary_1line":"CPI 발표","affected":[],"evidence":[]}]}'
    res = run_r2(_FakeClient(by_key={"theme:fed": j}), [_fresh(_news("n1", ["theme:fed"]))], CANDS, now=NOW)
    assert len(res.events) == 1 and res.events[0].type.value == "geopolitics"
    assert res.events[0].catalyst_type is not None and res.events[0].catalyst_type.value == "macro"


def test_run_r2_rejects_missing_summary() -> None:
    # 핵심 필드(summary) 누락만 폐기 + 사유 기록
    bad = '{"events":[{"event_type":"corp_action","catalyst_type":"earnings","scope":"single_stock",' \
          '"affected":[],"evidence":[]}]}'
    res = run_r2(_FakeClient(by_key={"001740": bad}), [_fresh(_news("n1", ["001740"]))], CANDS, now=NOW)
    assert res.rejected == 1 and res.events == []
    assert res.rejected_reasons and "summary" in res.rejected_reasons[0]


def test_run_r2_drops_out_of_range_affected_relevance() -> None:
    # affected relevance 범위밖 항목만 탈락, 이벤트는 유지
    j = '{"events":[{"event_type":"corp_action","catalyst_type":"supply_chain","scope":"single_stock",' \
        '"summary_1line":"수주","affected":[{"srtn_cd":"001740","relevance":0.9},' \
        '{"srtn_cd":"005930","relevance":5.0}],"evidence":[]}]}'
    res = run_r2(_FakeClient(by_key={"001740": j}), [_fresh(_news("n1", ["001740"]))], CANDS, now=NOW)
    assert len(res.events) == 1
    assert [a.srtn_cd for a in res.events[0].affected] == ["001740"]


def test_run_r2_excludes_stale_by_default() -> None:
    stale = NewsVerdict(item=_news("n1", ["001740"]), flags=frozenset({NewsFlag.STALE}))
    res = run_r2(_FakeClient(by_key={"001740": _GOOD}), [stale], CANDS, now=NOW)
    assert res.batches == 0 and res.events == []  # 호출조차 안 함


def test_run_r2_includes_stale_when_configured() -> None:
    stale = NewsVerdict(item=_news("n1", ["001740"]), flags=frozenset({NewsFlag.STALE}))
    res = run_r2(_FakeClient(by_key={"001740": _GOOD}), [stale], CANDS, now=NOW,
                 config=R2Config(include_stale=True))
    assert res.batches == 1 and len(res.events) == 1


def test_run_r2_batch_error_isolated() -> None:
    items = [_fresh(_news("n1", ["001740"])), _fresh(_news("n2", ["theme:fed"]))]
    client = _FakeClient(by_key={"001740": _GOOD, "theme:fed": "x"}, error_keys={"theme:fed"})
    res = run_r2(client, items, CANDS, now=NOW)
    assert len(res.events) == 1           # 001740 배치는 성공
    assert len(res.batch_errors) == 1 and res.batch_errors[0].startswith("theme:fed")
