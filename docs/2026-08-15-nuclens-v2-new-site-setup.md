# Nuclens V2 신규 사이트 전환 가이드

- 기본 Pages 프로젝트: `nuclens-v2`
- 기본 사이트: `https://nuclens-v2.pages.dev`
- `AUTOMATION_ENABLED` Repository Variable이 `true`가 아니면 정기 crawl/daily/weekly job은 실행되지 않는다.
- `workflow_dispatch` 수동 실행은 항상 가능하다.
- 첫 배포는 `Deploy web`만 수동 실행하여 사이트를 검증한다.
- V2 검증 후 `AUTOMATION_ENABLED=true`로 설정하고, 같은 시점에 V1의 crawl/daily/weekly를 비활성화한다.
- 과거 검색 연속성은 최신 V1의 `archive/`, `delivery_log.jsonl` 및 issue/trend 상태 파일을 cutover 직전에 V2로 복사해 확보한다.
