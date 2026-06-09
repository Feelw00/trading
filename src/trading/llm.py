"""LLM 호출 추상화 — Python 두뇌가 **직접 호출**(openclaw provider 라우팅 미사용, SCHED-3).

R2(분류·스코어)·R4/R5/R7이 공유. 기본 런타임은 ``claude -p`` 서브프로세스(로컬 Claude Code 인증,
외부 OpenAI 키 불필요 — OPEN_QUESTIONS NEWS-R2). 설계서 §2의 "GPT-5.5"는 서빙 모델 플레이스홀더였고,
모델·프로바이더 교체는 ``LLMClient`` 인터페이스 뒤 드롭인. **모델명은 .env 주입**(하드코딩 금지).

``claude -p --output-format json`` 실측 봉투(2026-06-09):
``{is_error, subtype, result, total_cost_usd, modelUsage, ...}`` — 텍스트=``.result``,
성공=``is_error==false && subtype=="success"``. (추측 아님 — 설치본 실측.)
"""

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class LLMError(RuntimeError):
    """LLM 호출 실패 — 프로세스 오류·타임아웃·is_error·봉투/JSON 파싱 실패."""


class LLMClient(Protocol):
    """LLM 표면 — 프롬프트 1개 → 텍스트 1개(결정론 호출, 스트리밍 없음)."""

    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class ClaudeCliClient:
    """``claude -p --output-format json`` 래퍼. ``runner`` 주입으로 테스트는 프로세스 없이 봉투 흉내."""

    model: str | None = None       # None이면 claude 기본 모델(.env 주입, 하드코딩 금지)
    binary: str = "claude"
    timeout_s: float = 120.0
    runner: Runner = subprocess.run

    def complete(self, prompt: str) -> str:
        argv = [self.binary, "-p", prompt, "--output-format", "json"]
        if self.model:
            argv += ["--model", self.model]
        try:
            proc = self.runner(argv, capture_output=True, text=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude -p 타임아웃({self.timeout_s}s)") from e
        if proc.returncode != 0:
            raise LLMError(f"claude -p 종료코드 {proc.returncode}: {(proc.stderr or '').strip()[:300]}")
        try:
            env: dict[str, Any] = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude -p 봉투 파싱 실패: {proc.stdout[:200]!r}") from e
        if env.get("is_error") or env.get("subtype") != "success":
            raise LLMError(f"claude -p 실패: subtype={env.get('subtype')} {str(env.get('result'))[:200]}")
        result = env.get("result")
        if not isinstance(result, str):
            raise LLMError(f"claude -p result 누락/형식오류: {type(result).__name__}")
        return result


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _first_json_start(s: str) -> int | None:
    cands = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    return min(cands) if cands else None


def extract_json(text: str) -> Any:
    """LLM 텍스트 → JSON 값. 코드펜스 제거 + 첫 ``{``/``[`` 부터 짝 맞는 끝까지(뒤 산문 무시)."""
    s = text.strip()
    if s.startswith("```"):
        s = _FENCE.sub("", s).strip()
    start = _first_json_start(s)
    if start is None:
        raise LLMError(f"JSON 없음: {text[:200]!r}")
    try:
        obj, _ = json.JSONDecoder().raw_decode(s[start:])
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 파싱 실패: {s[start:start + 200]!r}") from e
    return obj


def complete_json(client: LLMClient, prompt: str) -> Any:
    """complete → JSON 파싱(스키마-강제 출력 라운드 공용)."""
    return extract_json(client.complete(prompt))


def client_from_env(env: Mapping[str, str] | None = None) -> ClaudeCliClient:
    """.env에서 모델 주입(R2_MODEL → CLAUDE_MODEL, 미설정이면 None=claude 기본)."""
    e = env if env is not None else os.environ
    model = e.get("R2_MODEL") or e.get("CLAUDE_MODEL") or None
    return ClaudeCliClient(model=model)


__all__ = [
    "ClaudeCliClient",
    "LLMClient",
    "LLMError",
    "client_from_env",
    "complete_json",
    "extract_json",
]
