"""Object 기반 Event Identity — 가설 검증 실험 (2026-09-20).

가설: Event 의 신원 키는 (object_ref, transition, date_bucket) 셋이고 셋이 다 맞아야
같은 Event 다. 하나라도 모르면 "같다"가 아니라 "모른다"이며, 모르는 것은 grade 를
낮춰 현재 경로(production 의 묶음)로 후퇴한다.

이 패키지는 production 어디에도 연결되지 않는다. `web/build_data.py` · `daily_brief.py` ·
원장 · 캐시 어느 것도 쓰지 않고(읽기만 한다), LLM · 네트워크 호출이 없다.

    objects.py   — Object 추출. 결정적 v0(호기·발전소·레지스트리 project) + 실험용 어휘표 v0-x
    resolver.py  — Event resolver. 결정 계층 0(거부권) → 1(Object) → 2(transition·날짜) → 3(원장 조회)
    replay.py    — 과거 데이터 전체 재생, production 분할과 대조, 지표·검토표 산출
"""
