"""보고서 웹 뷰 — 색인 생성·경로 탈출 차단·md 래핑 테스트(서버 기동 없이 순수 함수)."""

from pathlib import Path

from trading.report_site import build_index, safe_path, wrap_markdown


def _seed(tmp_path: Path) -> Path:
    base = tmp_path / "reports"
    (base / "dossiers").mkdir(parents=True)
    (base / "weekly-20260828.html").write_text("<html>w</html>", encoding="utf-8")
    (base / "weekly-20260828.md").write_text("# w", encoding="utf-8")
    (base / "dossiers" / "20260828-011780.md").write_text("# d", encoding="utf-8")
    (base / "v03-progress-report.html").write_text("<html>p</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("비밀", encoding="utf-8")
    return base.resolve()


def test_index_lists_all_groups(tmp_path: Path) -> None:
    base = _seed(tmp_path)
    idx = build_index(base)
    assert "weekly-20260828.html" in idx
    assert "dossiers/20260828-011780.md" in idx
    assert "v03-progress-report.html" in idx
    assert "secret.txt" not in idx


def test_safe_path_blocks_traversal(tmp_path: Path) -> None:
    base = _seed(tmp_path)
    ok = safe_path(base, "/dossiers/20260828-011780.md")
    assert ok is not None and ok.name == "20260828-011780.md"
    assert safe_path(base, "/../secret.txt") is None          # 디렉터리 탈출
    assert safe_path(base, "/%2e%2e/secret.txt") is None      # 인코딩 우회
    assert safe_path(base, "/없는파일.md") is None
    assert safe_path(base, "/dossiers") is None               # 디렉터리는 파일 아님


def test_markdown_wrapped_and_escaped(tmp_path: Path) -> None:
    body = wrap_markdown("# 제목 <script>", "x.md")
    assert "&lt;script&gt;" in body and "<script>" not in body.replace("&lt;script&gt;", "")
