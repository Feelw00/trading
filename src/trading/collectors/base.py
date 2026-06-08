"""수집기 공통 — HTTP(재시도·백오프), KST, 적재 레코드, SQLite append-only 라이터.

R0 수집기 공통 규약(CLAUDE.md): 멱등성·백오프, as_of/fetched_at/source 필수, KST tz-aware.
수집 결과는 ``.runtime/collect/<날짜>/<cluster>.sqlite`` 에 append-only 적재(landing).
타입화·계약(FactRecord) 변환은 후속(R2) — 여기선 원시 문자열로 보관.
"""

import json
import sqlite3
import time
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, field_validator

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(tz=KST)


class CollectError(RuntimeError):
    """수집 실패(네트워크·소스 에러). 호출측은 blocked로 기록하고 대체하지 않는다."""


# (url, timeout) -> raw bytes. 테스트에서 주입 가능.
Opener = Callable[[str, float], bytes]


def _urlopen(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data: bytes = resp.read()
    return data


def fetch_json(
    url: str,
    *,
    timeout: float = 10.0,
    retries: int = 3,
    backoff: float = 0.5,
    opener: Opener = _urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> Any:
    """GET → JSON. 일시 오류는 지수 백오프로 재시도, 최종 실패는 CollectError."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return json.loads(opener(url, timeout))
        except (OSError, ValueError) as exc:  # URLError/Timeout(OSError), JSONDecodeError(ValueError)
            last = exc
            if attempt < retries - 1:
                sleeper(backoff * (2**attempt))
    raise CollectError(f"fetch failed: {url}") from last


class CollectedFact(BaseModel):
    """수집 landing 1행. SQLite ``facts`` 테이블과 1:1. 값은 원시 문자열(타입화는 R2).

    ``as_of`` 는 소스가 준 데이터 시점 라벨(일별 시리즈면 날짜 문자열) — 계약 변환 시 tz 부여.
    ``fetched_at`` 은 수집 시각으로 반드시 KST aware.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster: str
    name: str
    metric: str
    source: str
    as_of: str
    fetched_at: datetime
    value: str | None = None
    unit: str | None = None
    region: str | None = None
    asset_class: str | None = None
    sector: str | None = None
    ticker: str | None = None
    verified: bool = False
    note: str | None = None

    @field_validator("fetched_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware (KST)")
        return v

    def as_row(self) -> tuple[Any, ...]:
        return (
            self.cluster,
            self.region,
            self.asset_class,
            self.sector,
            self.ticker,
            self.name,
            self.metric,
            self.value,
            self.unit,
            self.source,
            self.as_of,
            self.fetched_at.isoformat(),
            int(self.verified),
            self.note,
        )


FACTS_DDL = """
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cluster TEXT NOT NULL, region TEXT, asset_class TEXT, sector TEXT, ticker TEXT, name TEXT,
  metric TEXT NOT NULL, value TEXT, unit TEXT,
  source TEXT NOT NULL, as_of TEXT NOT NULL, fetched_at TEXT NOT NULL,
  verified INTEGER NOT NULL DEFAULT 0, note TEXT
)
"""

_INSERT = (
    "INSERT INTO facts (cluster, region, asset_class, sector, ticker, name, metric, "
    "value, unit, source, as_of, fetched_at, verified, note) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def write_facts(db_path: Path, facts: Sequence[CollectedFact]) -> int:
    """append-only INSERT. 수정/삭제 없음 — 정정은 새 행으로."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(FACTS_DDL)
        conn.executemany(_INSERT, [f.as_row() for f in facts])
        conn.commit()
    finally:
        conn.close()
    return len(facts)


def default_db_path(cluster: str, *, day: datetime | None = None) -> Path:
    d = (day or now_kst()).strftime("%Y-%m-%d")
    return Path(".runtime") / "collect" / d / f"{cluster}.sqlite"


__all__ = [
    "KST",
    "CollectError",
    "CollectedFact",
    "FACTS_DDL",
    "Opener",
    "default_db_path",
    "fetch_json",
    "now_kst",
    "write_facts",
]
