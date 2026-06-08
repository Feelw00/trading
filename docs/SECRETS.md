# 시크릿 관리 규칙

이 프로젝트는 개인 GitHub(**public 가능성 포함**)에 올라간다.
**비밀값은 어떤 경우에도 git에 들어가지 않는다.** 공유는 1Password로만 한다.

## 1. 원칙 (CLAUDE.md 연동)
- API 키, 증권사 계정/계좌번호, 토큰, DB 비밀번호 등은 코드·로그·테스트 픽스처에 하드코딩 금지.
- 모든 비밀값은 환경변수(`.env`)로 주입한다.
- 모델명도 하드코딩 금지 — `.env`로 주입.

## 2. 파일 구조
| 파일 | git | 내용 |
|---|---|---|
| `.env.example` | ✅ 커밋 | 키 이름 + 주석만, 값은 비움. "무엇을 채워야 하는지"의 명세. |
| `.env`         | ❌ ignore | 실제 비밀값. 로컬에만 존재. |

`.gitignore`가 `.env`, `.env.*`(단 `.env.example` 예외), `secrets/`, `*.key`, `*.pem`,
토큰 캐시(`.tokens/`, `*.token`, `.kis_token*`)를 차단한다.

## 3. 1Password 공유 워크플로 (수동 파일 방식)
**새 기기 / 재설치 시:**
1. 1Password에서 **"stock / .env"** 항목(보안 노트 또는 문서)을 연다.
2. 내용을 프로젝트 루트의 `.env`로 저장한다.
   - 보안 노트면: 본문 복사 → `.env`에 붙여넣기
   - 문서 첨부면: 다운로드 → 루트에 `.env`로 배치
3. `git status`에 `.env`가 안 보이는지 확인(ignore 정상 동작).

**값 변경/키 회전 시:**
1. 로컬 `.env` 수정
2. 1Password "stock / .env" 항목을 새 내용으로 갱신
3. 회전 날짜를 항목 노트에 기록

## 4. 사고 대응 (실수로 커밋한 경우)
- **즉시 해당 키를 회전(폐기·재발급)** 한다. git 히스토리 정리보다 회전이 우선.
- `git rm --cached <file>` → `.gitignore` 확인. push 전이면 히스토리 재작성(`git filter-repo`)이 더 쉽다.
- 이미 push 했다면 키는 유출된 것으로 간주하고 무조건 회전.

## 5. 향후 업그레이드 (선택): 1Password CLI
`op` CLI를 설치하면 플레인텍스트 `.env` 없이 운영 가능:
- `.env.tpl`에 `op://Private/stock-xxx/field` 참조를 커밋
- 실행: `op run --env-file=.env.tpl -- <cmd>` 또는 `op inject -i .env.tpl -o .env`
- 설치: `brew install 1password-cli`

현재는 **수동 파일 방식** 사용. 전환 시 이 문서를 갱신한다.
