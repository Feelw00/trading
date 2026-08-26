"""운영자 수동 입력 채널(PIVOT-8) — §4 가드 테스트."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading.collectors.manual import (
    ManualInputError,
    ManualStore,
    SurgeConfirmRequired,
    add_entry,
)

KST = ZoneInfo("Asia/Seoul")
AS_OF = datetime(2026, 8, 25, 15, 30, tzinfo=KST)
NOW = datetime(2026, 8, 26, 10, 0, tzinfo=KST)


def _store(tmp_path: Path) -> ManualStore:
    return ManualStore(tmp_path / "manual.sqlite")


def _add(store: ManualStore, value: float, **kw: object) -> object:
    defaults: dict[str, object] = {
        "metric": "shipping.bdi",
        "source": "manual:발틱해운거래소(공표치)",
        "as_of": AS_OF,
        "now": NOW,
    }
    defaults.update(kw)
    return add_entry(store, value=value, **defaults)  # type: ignore[arg-type]


def test_add_and_versioning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, 1400.0)
    _add(store, 1420.0)
    latest = store.latest("shipping.bdi")
    assert latest is not None and latest.version == 2 and latest.value == 1420.0
    assert [e.value for e in store.history("shipping.bdi")] == [1400.0, 1420.0]
    assert store.metrics() == ["shipping.bdi"]
    store.close()


def test_source_format_enforced(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ManualInputError, match="manual:"):
        _add(store, 1400.0, source="발틱해운거래소")  # prefix 없음
    with pytest.raises(ManualInputError):
        _add(store, 1400.0, source="manual:")  # 출처명 없음
    store.close()


def test_naive_and_future_as_of_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ManualInputError, match="timezone"):
        _add(store, 1400.0, as_of=datetime(2026, 8, 25, 15, 30))  # naive
    with pytest.raises(ManualInputError, match="미래"):
        _add(store, 1400.0, as_of=datetime(2026, 8, 27, 0, 0, tzinfo=KST))  # now보다 미래
    store.close()


def test_surge_guard_requires_confirm(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, 1400.0)
    with pytest.raises(SurgeConfirmRequired):
        _add(store, 3000.0)  # +114% — 오타 방어
    assert store.latest("shipping.bdi") is not None
    assert store.latest("shipping.bdi").version == 1  # type: ignore[union-attr]  # 저장 안 됨
    entry = _add(store, 3000.0, confirm=True)  # 확인 후 재시도
    assert getattr(entry, "version") == 2
    store.close()


def test_small_change_no_confirm_needed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, 1400.0)
    entry = _add(store, 1450.0)  # +3.6% — 가드 미발동
    assert getattr(entry, "version") == 2
    store.close()
