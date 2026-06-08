# 리플레이 픽스처

`<YYYY-MM-DD>/facts.json`(FactRecord 배열), `<YYYY-MM-DD>/events.json`(EventRecord 배열).
`trading.replay.harness.ReplayRunner` 가 날짜를 순회하며 검증 → `as_of` 시간순 정렬 → 저널에 append.

`sample/` 의 값은 **전부 가짜**입니다(`source: "sample_fake"`, summary 앞에 `[SAMPLE/FAKE]`).
실데이터(6/2~6/8 주간 등)는 운영자가 별도 디렉터리(예: `fixtures/replay/2026-06-W1/`)에 같은 포맷으로 채운다.
