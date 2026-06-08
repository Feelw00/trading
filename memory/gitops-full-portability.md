---
name: gitops-full-portability
description: "모든 리소스를 git이 단일 소스로 관리, 다른 기기에 clone+bootstrap으로 즉시 이식 가능해야 함 (GitOps)"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7ea14fad-c8ef-45e3-8548-e7e1fb2a0f7c
---

Lucas는 트레이딩 프로젝트의 **모든 리소스를 git repo가 단일 소스(source of truth)로** 관리하길 원한다. 목적: 이 컴퓨터 외 **다른 기기에 그대로 이식**(clone → bootstrap → 1Password로 시크릿 주입 → 즉시 가동).

**Why:** 기기 종속 없이 어디서든 동일 시스템을 재현·운영하기 위함. repo만 있으면 전체 시스템이 복원돼야 함.

**How to apply:**
- openclaw 설정(cron/heartbeat/채널/openclaw.json)을 `~/.openclaw`에 손으로 두지 말고 **`ops/openclaw/`에 선언적 코드**로 두고 스크립트로 적용. `~/.openclaw`는 그 스크립트로 생성되는 **결과물**(원본 아님).
- `ops/bootstrap`이 새 기기 프로비저닝: openclaw 설치(핀된 Node 22.22)+poetry+config 적용+cron 등록+`.env`(1Password/op).
- **git에서 빠지는 것은 단 둘**: 비밀값(1Password), 생성되는 런타임 상태(gitignore). 그 외 전부 git.
- 재현성 위해 **버전 핀** 필수: Node 22.22, Python 3.13, openclaw 버전, `poetry.lock`.
- **트레이딩 전용 openclaw 인스턴스(프로젝트 `OPENCLAW_HOME`) 권장** — 개인 openclaw(`~/.openclaw`, 개인 auth·cron 섞임)와 분리해야 git 100% 관리·이식 가능.

관련: [[openclaw-integration-pending]]
