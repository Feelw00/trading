"""알림 계약 — P0/P1/P2 + 페이로드 4요소 강제 (설계서 §8).

원칙: **모든 알림은 사전 정의된 행동에 매핑된다.** "시장이 움직였다" 류의 행동 없는
알림은 알림 피로·즉흥 매매를 유도하므로 **생성 자체를 스키마가 거부**한다.
페이로드는 ``{무엇이, 어느 규칙을, 사전 약속된 행동은, 기한은}`` 4요소 고정.

- P0(즉시 푸시)·P1(점심·마감 다이제스트): ``action``/``deadline`` 비면 ValidationError.
- P2(정보, 보고서 포함만): 매핑 행동이 **없음** — ``action``/``deadline`` 이 있으면 거부
  (행동이 필요한 알림을 P2로 강등해 숨기는 것도, 행동 없는 알림을 P0로 올리는 것도 차단).

알림은 신규 주문 초안을 생성하지 않는다(§8) — 이 모듈엔 주문 관련 코드가 존재하지 않는다.
"""

import html
from enum import Enum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading.collectors.base import now_kst
from trading.contracts.base import NonEmptyStr


class Severity(str, Enum):
    P0 = "P0"  # 즉시 푸시 — 무효화 발동·스탑 체결, 서킷브레이커, 환율 임계, 바이너리 전이, 보유 공시
    P1 = "P1"  # 묶음(점심·마감) — 주문 레벨 접근, 수급 전환 징후, 플레이북 arm/발동
    P2 = "P2"  # 정보 — 보고서 포함만, 푸시 없음


class Alert(BaseModel):
    """알림 페이로드 — 4요소 고정 + 등급. 불변(frozen), 스키마 외 필드 거부."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: Severity
    what: NonEmptyStr                 # 무엇이 (관측된 사실)
    rule: NonEmptyStr                 # 어느 규칙을 (발동된 규칙·임계)
    action: str = ""                  # 사전 약속된 행동 (P0/P1 필수)
    deadline: str = ""                # 기한 (P0/P1 필수)
    created_at: AwareDatetime = Field(default_factory=now_kst)

    @model_validator(mode="after")
    def _action_mapping(self) -> "Alert":
        if self.severity in (Severity.P0, Severity.P1):
            if not self.action.strip() or not self.deadline.strip():
                raise ValueError(
                    f"{self.severity.value} 알림은 action·deadline 필수 — "
                    "행동 매핑 없는 알림은 생성 불가(설계서 §8)"
                )
        else:  # P2 — 매핑 행동 없음(보고서 포함만)
            if self.action.strip() or self.deadline.strip():
                raise ValueError("P2는 정보 알림 — action/deadline 을 가질 수 없다(§8)")
        return self


def format_alert(alert: Alert) -> str:
    """채널 발송용 평문 — 4요소 고정 순서. (로그 폴백·비텔레그램 채널용)"""
    lines = [f"[{alert.severity.value}] {alert.what}", f"규칙: {alert.rule}"]
    if alert.severity is not Severity.P2:
        lines += [f"행동: {alert.action}", f"기한: {alert.deadline}"]
    lines.append(f"({alert.created_at.isoformat(timespec='minutes')})")
    return "\n".join(lines)


def format_alert_html(alert: Alert) -> str:
    """Telegram parse_mode=HTML 발송용 — 평문과 동일 4요소, 등급·행동만 강조.

    전 필드 HTML 이스케이프(환율 임계 "<1540" 류 안전). 텔레그램은 마크다운을
    렌더하지 않으므로(R6과 동일) 서식은 HTML 엔티티로만.
    """
    lines = [
        f"<b>[{alert.severity.value}] {html.escape(alert.what, quote=False)}</b>",
        f"규칙: {html.escape(alert.rule, quote=False)}",
    ]
    if alert.severity is not Severity.P2:
        lines += [
            f"행동: <b>{html.escape(alert.action, quote=False)}</b>",
            f"기한: {html.escape(alert.deadline, quote=False)}",
        ]
    lines.append(f"({alert.created_at.isoformat(timespec='minutes')})")
    return "\n".join(lines)


__all__ = ["Alert", "Severity", "format_alert", "format_alert_html"]
