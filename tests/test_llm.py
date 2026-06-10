"""LLM 클라이언트 추상화 — claude -p 봉투 파싱·오류·JSON 추출(프로세스 주입)."""

import json
import subprocess
from typing import Any

import pytest

from trading.llm import (
    ClaudeCliClient,
    LLMError,
    Runner,
    client_from_env,
    complete_json,
    extract_json,
)


def _envelope(result: str = "ok", *, is_error: bool = False, subtype: str = "success") -> str:
    return json.dumps(
        {"type": "result", "subtype": subtype, "is_error": is_error, "result": result}
    )


def _runner(stdout: str = "", *, returncode: int = 0, stderr: str = "", sink: list[Any] | None = None) -> Runner:
    def run(argv: list[str], **kw: Any) -> "subprocess.CompletedProcess[str]":
        if sink is not None:
            sink.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)

    return run


def test_complete_success() -> None:
    client = ClaudeCliClient(runner=_runner(_envelope("hello")))
    assert client.complete("hi") == "hello"


def test_model_flag_passed_when_set() -> None:
    sink: list[Any] = []
    client = ClaudeCliClient(model="claude-haiku-4-5", runner=_runner(_envelope(), sink=sink))
    client.complete("hi")
    assert "--model" in sink[0] and "claude-haiku-4-5" in sink[0]
    assert "--output-format" in sink[0] and "json" in sink[0]


def test_no_model_flag_when_unset() -> None:
    sink: list[Any] = []
    ClaudeCliClient(runner=_runner(_envelope(), sink=sink)).complete("hi")
    assert "--model" not in sink[0]


def test_nonzero_exit_raises() -> None:
    client = ClaudeCliClient(runner=_runner("", returncode=1, stderr="boom"))
    with pytest.raises(LLMError, match="종료코드"):
        client.complete("hi")


def test_is_error_envelope_raises() -> None:
    client = ClaudeCliClient(runner=_runner(_envelope(is_error=True)))
    with pytest.raises(LLMError):
        client.complete("hi")


def test_non_success_subtype_raises() -> None:
    client = ClaudeCliClient(runner=_runner(_envelope(subtype="error_max_turns")))
    with pytest.raises(LLMError):
        client.complete("hi")


def test_bad_envelope_json_raises() -> None:
    client = ClaudeCliClient(runner=_runner("not json"))
    with pytest.raises(LLMError, match="봉투"):
        client.complete("hi")


def test_timeout_raises() -> None:
    def run(argv: list[str], **kw: Any) -> "subprocess.CompletedProcess[str]":
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1.0)

    with pytest.raises(LLMError, match="타임아웃"):
        ClaudeCliClient(runner=run).complete("hi")


def test_extract_json_plain() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('```\n[1, 2]\n```') == [1, 2]


def test_extract_json_trailing_prose() -> None:
    assert extract_json('{"a": 1}\n\n위 결과입니다.') == {"a": 1}


def test_extract_json_none_raises() -> None:
    with pytest.raises(LLMError, match="JSON 없음"):
        extract_json("그냥 텍스트")


def test_complete_json_end_to_end() -> None:
    client = ClaudeCliClient(runner=_runner(_envelope('```json\n{"x": 9}\n```')))
    assert complete_json(client, "go") == {"x": 9}


def test_client_from_env_model_precedence() -> None:
    assert client_from_env({"R2_MODEL": "m1", "CLAUDE_MODEL": "m2"}).model == "m1"
    assert client_from_env({"CLAUDE_MODEL": "m2"}).model == "m2"
    assert client_from_env({}).model is None


def test_extract_json_skips_prose_brackets() -> None:
    # 결함②(2026-06-10): 산문 속 [기사id] 가짜 시작점을 건너뛰고 진짜 JSON을 찾는다
    text = '[9bc8b78c06d38fdb]: 발행 2026-06-09 분석 결과 {"survived": false, "reason": "기반영"}'
    assert extract_json(text) == {"survived": False, "reason": "기반영"}


class _FlakyClient:
    def __init__(self, fail_times: int, payload: str = '{"ok": 1}') -> None:
        self.fail_times = fail_times
        self.payload = payload
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise LLMError("일시 실패")
        return self.payload


def test_complete_json_retries_once_then_succeeds() -> None:
    c = _FlakyClient(fail_times=1)
    assert complete_json(c, "go") == {"ok": 1}
    assert c.calls == 2


def test_complete_json_exhausts_retries_and_raises() -> None:
    c = _FlakyClient(fail_times=5)
    with pytest.raises(LLMError):
        complete_json(c, "go")
    assert c.calls == 2  # 기본 1회 재시도

    c0 = _FlakyClient(fail_times=5)
    with pytest.raises(LLMError):
        complete_json(c0, "go", retries=0)
    assert c0.calls == 1


def test_client_from_env_timeout() -> None:
    assert client_from_env({"CLAUDE_TIMEOUT_S": "450"}).timeout_s == 450.0
    assert client_from_env({}).timeout_s == 300.0
