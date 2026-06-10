"""R7 — 결정론 채점기·캘리브레이션·R4 정확도·레짐 프록시 테스트 (순수 코드)."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading.collectors.market import MarketStore
from trading.contracts.event import (
    AffectedStock,
    EventRecord,
    EventType,
    LensVerdict,
    Scope,
    Verification,
)
from trading.contracts.thesis import Direction, Persona
from trading.journal.scores import ScoreStore
from trading.rounds.r7 import R7Config, evaluate, regime_ratio, score_thesis
from test_r5 import _thesis

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 20, 10, 0, tzinfo=KST)

# 거래일 6일: 6/10(논제일) → 6/11 entry → 6/16 exit(horizon 3 기준 6/11+3=6/16)
DATES = ["20260610", "20260611", "20260612", "20260615", "20260616", "20260617"]


def _store(tmp_path: Path, closes: dict[str, list[float]], flt: float = 1.0) -> MarketStore:
    """일자별 종가 시리즈를 가진 시세 스토어 픽스처."""
    ms = MarketStore(tmp_path / "m.sqlite")
    rows = []
    for srtn, series in closes.items():
        for d, c in zip(DATES, series):
            rows.append(
                {
                    "basDt": d, "srtnCd": srtn, "itmsNm": f"종목{srtn}", "mrktCtg": "KOSPI",
                    "clpr": str(c), "fltRt": str(flt), "vs": "0", "mkp": str(c),
                    "hipr": str(c), "lopr": str(c), "trqu": "1", "trPrc": "1",
                    "lstgStCnt": "1", "mrktTotAmt": "1", "isinCd": "x", "basDtKey": "",
                }
            )
    ms.upsert(rows)
    return ms


def _t(direction: Direction = Direction.LONG, *, conf: float = 0.6, horizon: int = 3) -> Any:
    return _thesis(
        id="thesis.20260610.001740.supply",
        as_of=datetime(2026, 6, 10, 20, 0, tzinfo=KST),
        direction=direction, confidence=conf, horizon_days=horizon,
    )


def test_long_hit_scored(tmp_path: Path) -> None:
    ms = _store(tmp_path, {"001740": [100, 100, 102, 104, 110, 111]})
    o = score_thesis(_t(Direction.LONG), ms, DATES)
    ms.close()
    # entry=6/11(100) → exit=6/16(110): +10% → long 적중
    assert o.status == "scored" and o.hit is True
    assert o.realized_pct is not None and abs(o.realized_pct - 10.0) < 1e-9


def test_short_miss_on_rally(tmp_path: Path) -> None:
    ms = _store(tmp_path, {"001740": [100, 100, 102, 104, 110, 111]})
    o = score_thesis(_t(Direction.SHORT), ms, DATES)
    ms.close()
    assert o.status == "scored" and o.hit is False


def test_immature_not_scored(tmp_path: Path) -> None:
    ms = _store(tmp_path, {"001740": [100, 100, 102, 104, 110, 111]})
    o = score_thesis(_t(horizon=10), ms, DATES)  # 시계 미도래
    ms.close()
    assert o.status == "immature" and o.hit is None


def test_flat_and_no_data_separated(tmp_path: Path) -> None:
    ms = _store(tmp_path, {"999999": [1, 1, 1, 1, 1, 1]})  # 대상 종목 데이터 없음
    assert score_thesis(_t(Direction.FLAT), ms, DATES).status == "flat"
    assert score_thesis(_t(), ms, DATES).status == "no_data"
    ms.close()


def _event(*, confirmed: bool, srtn: str = "001740") -> EventRecord:
    return EventRecord(
        id=f"evt.20260610.{srtn}.00",
        as_of=datetime(2026, 6, 10, 18, 0, tzinfo=KST),
        fetched_at=NOW, source="r2:claude", type=EventType.CORP_ACTION,
        summary_1line="촉매", entities=[srtn], scope=Scope.SINGLE_STOCK,
        catalyst_strength=0.5,
        affected=[AffectedStock(srtn_cd=srtn, relevance=1.0)],
        verification=Verification(
            verified_by="r4", confirmed=confirmed,
            lens_verdicts=[LensVerdict(lens="strength", survived=confirmed, reason="x")],
        ),
    )


def test_evaluate_r4_accuracy_and_record(tmp_path: Path) -> None:
    # 001740: 6/11(100) → 6/16(110) +10% (이동 큼) / 005930: 100 → 100.5 (이동 없음)
    ms = _store(tmp_path, {"001740": [100, 100, 102, 104, 110, 111],
                           "005930": [100, 100, 100.2, 100.3, 100.5, 100.4]})
    events = [_event(confirmed=True, srtn="001740"), _event(confirmed=False, srtn="005930")]
    record, outcomes = evaluate([_t()], events, ms, now=NOW)
    ms.close()
    # 생존 이벤트(001740)가 크게 움직임 → 생존 정확 / 기각(005930) 무이동 → 기각 정확
    # (window=3: 6/11→6/16, threshold 3%)
    assert record.r4_confirmed_checked == 1 and record.r4_confirmed_correct == 1
    assert record.r4_refuted_checked == 1 and record.r4_refuted_correct == 1
    supply = next(p for p in record.personas if p.persona is Persona.SUPPLY)
    assert supply.n_scored == 1 and supply.n_hit == 1 and supply.hit_rate == 1.0
    # 캘리브레이션: conf 0.6 → [0.55, 0.7) 버킷
    bucket = next(b for b in supply.calibration if b.lo == 0.55)
    assert bucket.n == 1 and bucket.hits == 1
    assert "트리거 무관" in record.notes[0]  # 측정 한계 명시


def test_regime_ratio_detects_volatility_jump(tmp_path: Path) -> None:
    ms = MarketStore(tmp_path / "m.sqlite")
    rows = []
    flt_by_day = {"20260610": 1.0, "20260611": 1.0, "20260612": 1.0,
                  "20260615": 1.0, "20260616": 3.0, "20260617": 3.0}
    for d, f in flt_by_day.items():
        for srtn in ("001740", "005930", "000660"):
            rows.append({"basDt": d, "srtnCd": srtn, "itmsNm": "x", "mrktCtg": "KOSPI",
                         "clpr": "100", "fltRt": str(f), "vs": "0", "mkp": "100",
                         "hipr": "100", "lopr": "100", "trqu": "1", "trPrc": "1",
                         "lstgStCnt": "1", "mrktTotAmt": "1", "isinCd": "x"})
    ms.upsert(rows)
    ratio = regime_ratio(ms, list(flt_by_day), R7Config(regime_recent_days=2, regime_baseline_days=4))
    ms.close()
    assert ratio is not None and ratio > 2.0  # 최근 2일 3.0 vs 기준 1.0


def test_regime_insufficient_data_is_none(tmp_path: Path) -> None:
    ms = _store(tmp_path, {"001740": [100, 100, 102, 104, 110, 111]})
    assert regime_ratio(ms, DATES[:2], R7Config()) is None
    ms.close()


def test_score_store_roundtrip(tmp_path: Path) -> None:
    ms = _store(tmp_path, {"001740": [100, 100, 102, 104, 110, 111]})
    record, _ = evaluate([_t()], [], ms, now=NOW)
    ms.close()
    ss = ScoreStore(tmp_path / "s.sqlite")
    assert ss.append(record) == 1
    back = ss.latest()
    assert back is not None and back.id == record.id
    assert ss.append(record) == 2  # 재평가=새 version
    ss.close()
