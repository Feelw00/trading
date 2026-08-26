"""`python -m trading.valuation` — R2 밸류에이션 산출(DB-first, 외부 호출 없음)."""

from trading.valuation.build import main

if __name__ == "__main__":
    raise SystemExit(main())
