"""PositionRecord — 보유 포지션 + 계획 스냅샷 (P-8, 설계서 §8 "보유 포지션 무효화 잔여 거리").

운영자가 실제 체결한 보유를 **수동 등록**한다(KIS 잔고·체결 어댑터 미구현 — 잔고 대사는 후속).
핵심은 수량·평단이 아니라 **계획의 박제**: discuss/플레이북이 만든 조건문(가설·트리거·무효화·
스탑·시간손절·확신도)과 분석 문서 전문을 포지션에 묶어, 보유 중 점검(arm-check·저녁 보고)이
"왜 샀고 언제 나가기로 했는지"를 매일 다시 들이밀게 한다.

스탑·시간손절은 코드가 평가(잔여 거리·도래일), 자유문 무효화 조건은 코드가 평가하지 않고
표시만 — 해석은 스킬(LLM)·판단은 운영자(절대금지 #2 정합).
"""

from enum import Enum

from pydantic import Field

from trading.contracts.base import BaseRecord, NonEmptyStr
from trading.contracts.order import Side


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class PositionRecord(BaseRecord):
    symbol: NonEmptyStr                      # 6자리 단축코드
    side: Side = Side.BUY                    # 보유 방향(현물 long=buy)
    qty: int = Field(gt=0)
    avg_price: float = Field(gt=0)           # 평균 단가(원)
    # --- 계획 스냅샷 (discuss 조건문 — "분석 문서 그대로 저장") ---
    hypothesis: str = ""                     # 가설 1줄 + 방향
    trigger_text: str = ""                   # 진입 트리거(기록 — 실제 진입 근거)
    invalidation_text: str = ""              # 무효화 조건(자유문 — 표시·스킬 대조용, 코드 미평가)
    stop_level: float | None = Field(default=None, gt=0)   # 가격 스탑(코드 평가)
    time_stop_days: int | None = Field(default=None, gt=0)  # 시간손절(거래일, 코드 평가)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    plan_doc: str = ""                       # 분석 문서 전문(마크다운 박제)
    source_ref: str = ""                     # 출처(discuss pack 버전·플레이북/주문 id 등)
    # --- 상태 (전이는 새 version append) ---
    status: PositionStatus = PositionStatus.OPEN
    close_reason: str = ""                   # 정리 사유(스탑/시간손절/무효화/임의 — closed에서만 의미)


__all__ = ["PositionRecord", "PositionStatus"]
