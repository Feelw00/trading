"""시총 스냅샷 소급 백필(P-17 ① — 운영자 지시 2026-08-31) — 순수 코드·승인 어댑터만.

밴드 PBR 축의 시세 창을 2016~으로 확장한다(재무 하한과 정합). 원료:
- 연말 종가: 토스 캔들 ``GET /api/v1/candles`` (interval=1d, **adjusted=false** — 당시
  주식수와 짝을 맞추려면 미수정 종가여야 한다). 실호출 관측: 일봉 1985~ 소급.
- 발행주식총수: DART ``stockTotqySttus`` (se=보통주, stlm_dt=당해 12월 — 12월 결산만 수용).
- cap = 미수정 연말 종가 × 보통주 발행주식총수 → ``market.sqlite`` 의 ``cap_snapshots``
  (daily_quotes와 분리 — 연속성 가드가 2016~2019 무자료 시대를 갭으로 오인하지 않게).

검증 모드 ``--validate <연도>``: daily_quotes가 있는 연말(예: 2020)에 파생 cap을 공식
mrkt_tot_amt와 대조한다(쓰기 없음). 소급 기록 전 반드시 1회 통과시킬 것.

사용:
  python -m trading.backfill_caps --validate 2020 [--limit 40]
  python -m trading.backfill_caps --years 2016-2019
"""

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trading.collectors.dart import DartClient
from trading.collectors.fins import FinStore
from trading.collectors.market import MarketStore
from trading.collectors.toss import TossClient, client_from_env

SOURCE = "derived:toss-candles+dart-stockTotqySttus"


def parse_count(raw: Any) -> int | None:
    """DART 수량 필드("140,679,337" / "-" / None) → int|None. 추측 금지 — 비정상은 None."""
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def pick_year_end_bar(payload: dict[str, Any], year: str) -> tuple[str, float] | None:
    """캔들 봉투에서 당해 12월 마지막 봉 → (YYYYMMDD, 종가). 없으면 None."""
    rows = payload.get("candles")
    if not isinstance(rows, list):
        return None
    best: tuple[str, float] | None = None
    for r in rows:
        ts = str(r.get("timestamp") or "")
        if not ts.startswith(f"{year}-12"):
            continue
        try:
            close = float(r.get("closePrice"))
        except (TypeError, ValueError):
            continue
        ymd = ts[:10].replace("-", "")
        if best is None or ymd > best[0]:
            best = (ymd, close)
    return best


def common_shares(rows: list[dict[str, Any]], year: str) -> int | None:
    """주식총수 행에서 보통주 발행주식총수(istc_totqy). 12월 결산(stlm_dt=당해 12월)만."""
    for r in rows:
        if str(r.get("se") or "").strip() != "보통주":
            continue
        if not str(r.get("stlm_dt") or "").startswith(f"{year}-12"):
            return None  # 비 12월 결산 — 연말 종가와 시점 불일치라 정직 제외
        return parse_count(r.get("istc_totqy"))
    return None


@dataclass
class YearResult:
    year: str
    ok: list[tuple[str, str, float, int, float, str]]  # cap_snapshots 행
    no_candle: int = 0
    no_shares: int = 0


def _load_env_keys() -> None:
    """수집 CLI 단독 실행용 — .env의 어댑터 키만 주입(값 미출력)."""
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        for key in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "DART_API_KEY"):
            if line.startswith(f"{key}=") and not os.environ.get(key):
                os.environ[key] = line.split("=", 1)[1].strip()


def collect_year(
    toss: TossClient,
    dart: DartClient,
    corp_map: dict[str, tuple[str, str]],
    symbols: list[str],
    year: str,
) -> YearResult:
    res = YearResult(year=year, ok=[])
    for sym in symbols:
        corp = corp_map.get(sym)
        if corp is None:
            res.no_shares += 1
            continue
        bar = pick_year_end_bar(
            toss.candles(sym, count=10, before=f"{year}-12-31T23:59:59+09:00", adjusted=False),
            year,
        )
        if bar is None:
            res.no_candle += 1
            continue
        shares = common_shares(dart.stock_totals(corp[0], year), year)
        if shares is None or shares <= 0:
            res.no_shares += 1
            continue
        ymd, close = bar
        res.ok.append((ymd, sym, close, shares, close * shares, SOURCE))
    return res


def validate(res: YearResult, market: MarketStore, tol: float = 0.02) -> int:
    """파생 cap을 공식 mrkt_tot_amt와 대조 — 상대오차 tol 이내 비율을 보고."""
    if not res.ok:
        print(f"검증 실패: {res.year} 파생 표본 없음")
        return 1
    ymd = max(r[0] for r in res.ok)
    official = market.quotes_on(ymd)
    diffs: list[tuple[float, str]] = []
    missing = 0
    for _d, sym, _c, _s, cap, _src in res.ok:
        ref = official.get(sym)
        if ref is None or ref <= 0:
            missing += 1
            continue
        diffs.append((abs(cap - ref) / ref, sym))
    if not diffs:
        print(f"검증 실패: {res.year} 공식 시총과 겹치는 표본 없음")
        return 1
    diffs.sort()
    within = sum(1 for d, _s in diffs if d <= tol)
    median = diffs[len(diffs) // 2][0]
    print(
        f"검증 {res.year}: 표본 {len(diffs)} (공식 결측 {missing}) · "
        f"중앙 상대오차 {median:.2%} · ±{tol:.0%} 이내 {within}/{len(diffs)}"
    )
    for d, sym in diffs[-5:]:
        print(f"  최대 오차: {sym} {d:.1%}")
    passed = median <= 0.005 and within / len(diffs) >= 0.95
    print("→ PASS" if passed else "→ FAIL (소급 기록 금지 — 원인 규명 먼저)")
    return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", default=None, help="소급 연도 범위 예: 2016-2019")
    ap.add_argument("--validate", default=None, help="검증 연도(daily_quotes 보유 연말, 쓰기 없음)")
    ap.add_argument("--limit", type=int, default=None, help="표본 수 제한(검증·리허설용)")
    args = ap.parse_args()

    _load_env_keys()
    toss = client_from_env()
    dart_key = os.environ.get("DART_API_KEY", "")
    if toss is None or not dart_key:
        print("키 미설정(TOSS_CLIENT_ID/SECRET·DART_API_KEY) — blocked")
        return 2
    dart = DartClient(dart_key)

    fins, market = FinStore(), MarketStore()
    try:
        symbols = fins.symbols()
        if args.limit:
            symbols = symbols[: args.limit]
        corp_map = dart.corp_code_map()

        if args.validate:
            res = collect_year(toss, dart, corp_map, symbols, args.validate)
            print(f"{args.validate}: 파생 {len(res.ok)} · 캔들 없음 {res.no_candle} · 주식수 없음 {res.no_shares}")
            return validate(res, market)

        if not args.years:
            print("--years 또는 --validate 필요")
            return 2
        lo, _, hi = args.years.partition("-")
        years = [str(y) for y in range(int(lo), int(hi or lo) + 1)]
        total = 0
        for year in years:
            res = collect_year(toss, dart, corp_map, symbols, year)
            n = market.upsert_cap_snapshots(res.ok)
            total += n
            print(
                f"{year}: 적재 {n}/{len(res.ok)} · 캔들 없음 {res.no_candle} · "
                f"주식수 없음 {res.no_shares}"
            )
        print(f"합계 {total}행 → cap_snapshots ({SOURCE})")
        return 0
    finally:
        fins.close()
        market.close()


if __name__ == "__main__":
    sys.exit(main())
