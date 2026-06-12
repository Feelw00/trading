"""OrderDraft·플레이북 결정론 해설 (순수 코드, LLM 없음).

운영자가 9~10시 집행 시점에 "이 주문이 무슨 뜻이냐"를 매번 코드에서 발굴하지 않도록,
흐름변수·트랜치·스탑·총량 상한을 사람이 읽는 한국어로 풀어준다(2026-06-12 피드백, P-6).

**판단이 아니라 번역이다** — 발동 여부(ACTIVE/INACTIVE)는 ``selector/engine`` 순수 코드가
결정하고, 이 모듈은 그 입력·결과를 설명만 한다(절대금지 #2). LLM 분석은 한 층 위(스킬).
"""

import re
from collections.abc import Sequence

from trading.contracts.order import OrderDraft, Stop, Tranche
from trading.contracts.playbook import FLOW_VARIABLES

# 흐름변수 → 한국어 설명. FLOW_VARIABLES(계약 화이트리스트)와 1:1 — 키 누락은 import 시점에 검출.
FLOW_VAR_KO: dict[str, str] = {
    "gap_pct": "시초 갭 크기(%)",
    "premkt_volume_ratio": "프리마켓 거래량 / 평시 비율",
    "premkt_volume_rank": "프리마켓 거래량 순위",
    "orderbook_imbalance": "호가 잔량 불균형(>0 매수우위)",
    "execution_strength": "체결강도(100 기준, 매수체결 우세도)",
    "auction_projection": "동시호가 예상체결가 궤적",
    "volume_climax": "거래량 클라이맥스(소진 신호)",
    "new_low_after": "신저가 갱신 시각 조건",
    "new_low_renewal_fail": "신저가 갱신 실패",
    "prev_day_high_reclaim": "전일 고가 회복(상승 확인)",
}

_MISSING = sorted(FLOW_VARIABLES - set(FLOW_VAR_KO))
if _MISSING:  # 화이트리스트에 변수가 추가됐는데 해설 사전을 안 채운 경우 — 조용한 결측 방지
    raise RuntimeError(f"FLOW_VAR_KO 해설 누락: {_MISSING} — explain.py 사전을 채워라")

_OP_KO = {">=": "이상", ">": "초과", "<=": "이하", "<": "미만", "==": "같음"}

# selector 계약: 조건식은 <op><숫자> (engine._COND와 동일 의미) — 그 외는 평가 불가
_COND = re.compile(r"^\s*(<=|>=|==|<|>)\s*(-?\d+(?:\.\d+)?)\s*$")


def var_label(var: str) -> str:
    """흐름변수 키 → '한국어설명(키)'. 미등록 키는 키 그대로(추측 금지)."""
    ko = FLOW_VAR_KO.get(var)
    return f"{ko}({var})" if ko else var


def explain_condition(var: str, expr: str) -> str:
    """'execution_strength', '>=110' → '체결강도(...) 110 이상'. 비숫자 조건은 평가불가 표기."""
    m = _COND.match(expr)
    if m is None:
        return f"{var_label(var)} {expr} (평가 불가 — 숫자 조건식 아님)"
    op, num = m.group(1), m.group(2)
    return f"{var_label(var)} {num} {_OP_KO[op]}"


def humanize_cap(cap: str | None) -> str:
    """'0.5 * normal_unit' → '기본단위의 50%'. 그 외 표현식은 원문 유지(추측 금지)."""
    m = re.match(r"^([0-9.]+)\s*\*\s*normal_unit$", cap or "")
    if not m:
        return cap or "(미지정)"
    return f"기본단위의 {float(m.group(1)) * 100:g}%"


_TRANCHE_KO = {
    "impatience_fee": "조급비용(소량 선진입)",
    "flush": "플러시(주력 — 투매 지정가 매집)",
    "confirmation": "확인(가격 상승 확인 후 추가)",
}


def explain_tranches(tranches: Sequence[Tranche]) -> list[str]:
    """3트랜치 진입 구조를 한 줄씩 — 비율·주문유형·조건(가격상승으로만 충족) 풀이."""
    out: list[str] = []
    for t in tranches:
        ko = _TRANCHE_KO.get(t.label, t.label)
        if t.condition is not None:
            cond = FLOW_VAR_KO.get(t.condition, t.condition)
            out.append(f"{t.pct_of_plan}% {ko} — {cond} 충족 시 집행")
        else:
            typ = t.order_type.value if t.order_type else "?"
            out.append(f"{t.pct_of_plan}% {ko} — {typ} 지정가")
    return out


def explain_stop(stop: Stop | None, time_stop_days: int | None) -> str:
    """손절 2종(가격·시간) 한 줄. 둘 중 가용한 것만 묶음."""
    parts: list[str] = []
    if stop is not None and stop.level is not None:
        parts.append(f"가격 스탑 {stop.level:g}(이탈 시 청산)")
    if time_stop_days is not None:
        parts.append(f"시간손절 {time_stop_days}거래일(미진행 시 정리)")
    return " / ".join(parts) if parts else "(손절 미지정)"


def draft_headline(draft: OrderDraft, *, name: str | None = None) -> str:
    """'엘티씨(170920) 매수' 식 헤드라인. side는 한국어."""
    label = f"{name}({draft.symbol})" if name else draft.symbol
    side_ko = {"buy": "매수", "sell": "매도"}.get(draft.side.value, draft.side.value)
    return f"{label} {side_ko}"


__all__ = [
    "FLOW_VAR_KO",
    "draft_headline",
    "explain_condition",
    "explain_stop",
    "explain_tranches",
    "humanize_cap",
    "var_label",
]
