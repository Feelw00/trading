"""openclaw.template.json → .runtime/openclaw/openclaw.json 렌더링.

env 치환 후 JSON 검증. 미치환 `${VAR}`이 남으면 실패(SAFE — 비밀값 누락 차단).
호출 전 ``.env`` 가 환경에 로드돼 있어야 한다(bootstrap.sh 가 source 후 실행).
"""

import json
import os
import re
import sys
from pathlib import Path
from string import Template

# 비밀값이 빠진 채 plaintext config가 생성되는 사고를 막기 위해
# 렌더 결과에 ``${VAR}`` 잔류가 있으면 실패한다.
_UNRESOLVED = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

_REPO = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPO / "ops/openclaw/openclaw.template.json"
_TARGET = _REPO / ".runtime/openclaw/openclaw.json"


def main() -> int:
    if not _TEMPLATE.is_file():
        print(f"missing template: {_TEMPLATE}", file=sys.stderr)
        return 1
    raw = Template(_TEMPLATE.read_text(encoding="utf-8"))
    rendered = raw.safe_substitute(os.environ)
    missing = sorted({m.group(1) for m in _UNRESOLVED.finditer(rendered)})
    if missing:
        print(f"unresolved env vars: {', '.join(missing)}", file=sys.stderr)
        return 1
    config = json.loads(rendered)  # 구문 검증
    _TARGET.parent.mkdir(parents=True, exist_ok=True)
    _TARGET.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _TARGET.chmod(0o600)
    print(f"rendered: {_TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
