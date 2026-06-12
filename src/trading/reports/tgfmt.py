"""보고 마크다운 → Telegram HTML 변환 (결정론, R6 템플릿 서브셋 전용).

텔레그램은 마크다운 원문을 렌더하지 않는다 — ``#``/``**``가 기호 그대로 노출(2026-06-11
운영자 피드백). Bot API가 공식 지원하는 ``parse_mode=HTML`` 로 변환한다.

변환 대상은 **R6 템플릿이 실제로 쓰는 서브셋만**(범용 마크다운 파서 아님):
``#``/``##`` 헤딩, ``**굵게**``, ```코드``` , ``- ``/``- [ ] `` 불릿, ``> `` 인용. 본문은 전부
HTML 이스케이프 — LLM 산출 텍스트(시나리오 등)에 ``<``/``&`` 가 섞여도 안전.

``코드`` → ``<code>`` 는 표시 외 효과가 하나 더 있다: 텔레그램은 엔티티가 걸린 구간에
자동 링크 감지를 적용하지 않는다 — ``order.<일자>.<종목>.buy`` 류 ID가 ``.buy`` gTLD 탓에
하이퍼링크로 오인되는 것을 차단한다(2026-06-12 운영자 피드백).
"""

import html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """이스케이프 후 **굵게**→<b>, `코드`→<code>만 — 그 외 인라인 문법은 평문 유지."""
    escaped = html.escape(text, quote=False)
    escaped = _CODE.sub(r"<code>\1</code>", escaped)
    return _BOLD.sub(r"<b>\1</b>", escaped)


def to_telegram_html(md: str) -> str:
    out: list[str] = []
    for raw in md.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")  # 연속 빈 줄 1개로 압축
            continue
        if stripped.startswith("##"):
            out.append(f"<b>{_inline(stripped.lstrip('#').strip())}</b>")
        elif stripped.startswith("#"):
            out.append(f"<b>{_inline(stripped.lstrip('#').strip())}</b>")
        elif stripped.startswith("> "):
            out.append(f"<i>{_inline(stripped[2:])}</i>")
        elif stripped.startswith("- [ ] "):
            out.append(f"□ {_inline(stripped[6:])}")
        elif stripped.startswith("- "):
            out.append(f"• {_inline(stripped[2:])}")
        else:
            out.append(_inline(stripped))
    return "\n".join(out).strip()


__all__ = ["to_telegram_html"]
