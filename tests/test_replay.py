"""AC: 리플레이 러너가 샘플 픽스처 2일치를 시간(as_of)순으로 주입하고 저널에 기록."""

from pathlib import Path

from trading.journal.store import InMemoryJournal
from trading.replay.harness import ReplayRunner

SAMPLE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "sample"


def test_replay_sample_injects_in_time_order() -> None:
    journal = InMemoryJournal()
    result = ReplayRunner(journal).run(SAMPLE_ROOT)

    assert result.facts_ingested == 3
    assert result.events_ingested == 2
    assert result.skipped == 0
    assert len(journal) == 5

    # as_of 비내림차순으로 저널에 기록됐는지
    as_ofs = [entry.record.as_of for entry in journal.entries]
    assert as_ofs == sorted(as_ofs)

    # 첫 주입은 6/2 팩트, 마지막은 6/3 이벤트
    assert result.order[0].startswith("sample.fact.")
    assert "2026-06-02" in result.order[0]
    assert result.order[-1] == "sample.evt.2026-06-03.0001"
