# 2026-07-14 — 부팅 재구조화: 읽기 전용 요약으로 전환 (유저 레벨 /boot 활용)

## 배경
- 19cf587에서 boot/end가 유저 레벨 세션 프로토콜(`~/.claude/commands/boot.md`·`end.md` + 프로젝트 `.claude/session.md`)로 이주됨.
  구조 확인: `/boot`는 work-boot를 직접 찾지 않는다 — **session.md `## boot`가 호출을 명시해서 실행**되는 구조.
- 운영자 지시: 구 boot의 1(콘솔 날짜)·2(collect-macro+regime)는 유지, **신선도 자동 수집(시세 collect·뉴스 collect-news)·
  자동 사이클 점검(drill --audit)은 부팅에서 제거**, 3·4를 통합해 스케줄러(cron 16슬롯) 축적분 요약만 제공.

## 산출
- `.claude/skills/work-boot/SKILL.md` 재작성 — **읽기 전용 요약**: ① 감시 풀 `approve --pool`(진입/손절/익절, 스크리너보다 먼저)
  ② 보유 포지션 `trading.positions`([정리 검토] 최상단) ③ 스크리너(stale 라벨·급변 시 순위 무효 명시 유지,
  갭 의심 시 수집하지 않고 ⚠️ + `/collect` 수동 안내) ④ history 읽기 ⑤ 체크리스트 ⑥ 다음 작업.
- `.claude/session.md` `## boot` 동기화: work-boot 라인을 "읽기 전용 요약"으로 교체 + gotchas에
  "부팅은 수집·점검 안 함 — 갭·미적재·WARN 의심 시 `/collect`·`/collect-news`·`drill.py --audit` 수동 실행" 추가.
- `### state`(감시기 heartbeat·게이트웨이 18790·EXEC_MODE·킬스위치)는 운영자 언급 없어 **유지**(live 안전 확인).

## 검증
- `/boot` 실측 1회 통과(10:51): 거시 11건 verified + regime NORMAL(KOSPI +0.5%/KOSDAQ -2.7% 실시간),
  감시기 arm-watch 가동·heartbeat 신선, 게이트웨이 18790 LISTEN, EXEC_MODE=live·킬스위치 없음,
  approved 풀 7건 다이제스트, 보유 없음, 스크리너 as_of=07-10 → "전일(7/13 급락) 미반영 — 사실상 무효" 라벨 정상 동작.

## 지시 에코백 (7/14 규칙)
- **부팅은 이제 수집·점검을 하지 않습니다** — 스케줄러가 쌓은 데이터를 읽어 요약만 합니다.
  시세 갭·뉴스 미적재·사이클 이상은 부팅이 자동으로 메워주지 않으므로 의심되면 `/collect`·`/collect-news`·`drill.py --audit`를 직접 실행해야 합니다.

## 미해결 / 후속
- `.claude/commands/collect.md` — 스킬 `collect`의 구버전 중복(섹터 태깅 3·4단계 누락, a98835a 잔재). **삭제 여부 운영자 확인 대기.**
- work-boot에서 빠진 drill --audit는 CURRENT "다음 세션 첫 작업" 2번(수동 실행)으로 이미 잡혀 있음.
