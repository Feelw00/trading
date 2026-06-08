"""DART 어댑터 — corp_code 매핑·공시·재무 + status 처리 검증(네트워크 없이)."""

import io
import zipfile
from typing import Any

import pytest

from trading.collectors.base import CollectError
from trading.collectors.dart import DartClient


def _zip(xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml.encode("utf-8"))
    return buf.getvalue()


def test_dart_corp_code_map_lists_only() -> None:
    xml = (
        "<result>"
        "<list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list>"
        "<list><corp_code>00999999</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list>"
        "</result>"
    )
    m = DartClient("k", bytes_fetch=lambda url: _zip(xml)).corp_code_map()
    assert m == {"005930": ("00126380", "삼성전자")}  # 비상장(stock_code 공백) 제외


def test_dart_corp_code_non_zip_raises() -> None:
    c = DartClient("k", bytes_fetch=lambda url: b'{"status":"100"}')
    with pytest.raises(CollectError):
        c.corp_code_map()


def test_dart_disclosures_ok() -> None:
    def jf(url: str) -> Any:
        assert "list.json" in url and "corp_code=00126380" in url
        return {"status": "000", "list": [{"report_nm": "분기보고서", "rcept_dt": "20260515"}]}

    rows = DartClient("k", json_fetch=jf).disclosures("00126380", "20260501", "20260608")
    assert len(rows) == 1 and rows[0]["report_nm"] == "분기보고서"


def test_dart_no_data_returns_empty() -> None:
    c = DartClient("k", json_fetch=lambda url: {"status": "013", "message": "데이터 없음"})
    assert c.disclosures("x", "20260101", "20260108") == []


def test_dart_error_status_raises() -> None:
    c = DartClient("k", json_fetch=lambda url: {"status": "020", "message": "사용한도 초과"})
    with pytest.raises(CollectError):
        c.financials("x", "2026", "11013")
