# 2026-08-14 Story Dedup & Ranking Redesign

## 목적

`nuclear-news-main`의 넓은 수집·discovery·기존 Gemini 호출 체계·설명 가능한 Python 랭킹은 유지한다.
`Daily News`에서 유효했던 알고리즘만 가져오되, 보도량을 조기 hard-cut으로 사용하지 않는다.

핵심 회귀 사례는 아래 두 Le Monde 기사다.

- `프랑스 EDF 원전 6기, 폭염으로 가동 중단…원전 가용성 20% 육박`
- `프랑스 원전 가동 중단: 장기 폭염, 고수온, 유량 감소가 원전 운영 위협`

문자열 제목은 다르지만 아침 브리핑에서는 동일한 EDF/프랑스/폭염·고수온 원전 운영 제약 story다.

## 변경된 선정 흐름

1. 기존 제목/토큰/호기 기반 빠른 dedup 유지
2. Gemini story dedup
   - 과거: `제목 + 매체`만 입력
   - 현재: 원문/한국어 제목 + summary + detail + tags + event_type + event_date + source tier + 기존 story context
   - 판정 단위를 `same article/event`에서 `same briefing story`로 확대
   - 단순 재전재=`duplicate`, 보완 분석/원인/수치 추가=`merge`
   - 승인·계약·재가동 등 새로운 독립 action은 별도 후속 story 유지
3. 동일 story의 대표 기사는 기존 랭킹 점수가 가장 높은 기사로 유지
4. 제거된 기사 정보는 버리지 않고 `story_*` metadata로 대표 기사에 합침
5. Daily News의 다중매체 신호를 `coverage_bonus`로 반영
   - 첫 매체는 0점: 단독 공식발표/특종 불이익 없음
   - 추가 독립 매체만 소폭 가점
   - 복수 tier1 확인은 추가 가점
   - 최대 +2점으로 제한: 보도량이 정책적 중요도를 압도하지 않음
6. 상위 후보에 최종 editorial redundancy check 한 번 더 수행
7. 최종 중복이 제거되면 `select_diverse()`를 다시 실행해 다음 순위 뉴스로 backfill

## 새 metadata

대표 기사에 필요 시 아래가 붙는다.

- `story_article_count`
- `story_outlet_count`
- `story_tier1_count`
- `story_independent_outlet_count`
- `story_sources`
- `story_related_titles`
- `story_context`
- `story_relation`
- `story_reason`
- `story_fingerprint`
- `story_dedup_stage`

발송된 기사에는 핵심 story metadata가 `delivery_log.jsonl`에도 기록된다.

## Daily News에서 가져오지 않은 것

- 트랙별 `Top 4 -> Top 3` 조기 절단: 중요 단독 공식발표/특종 recall을 해치므로 미이식
- 별도 Codex/Claude agent 체계: 모델/호출 인프라는 기존 `gemini_client.call_json()` 유지
- 별도 매체 가중치 지도: `sources.json`의 기존 source tier를 재사용해 이중 기준 방지

## 장애 시 동작

Gemini dedup 호출이 실패하거나 API 키가 없으면 기존처럼 fail-open(전량 유지)한다.
잘못 지워 빈 브리핑을 만드는 것보다 일부 중복이 남는 것을 우선한다.

## 회귀 테스트

`tests/test_story_dedup.py` 추가.

- Le Monde EDF 폭염 2건 story merge
- title-only가 아니라 summary/detail이 dedup 입력에 포함되는지
- outlet coverage는 가점이며 single-source gate가 아님
- 최종 중복 제거 후 다음 중요 기사 backfill

핵심 회귀 테스트:

```bash
python -m unittest tests.test_story_dedup tests.test_ranking tests.test_daily_brief
```

2026-08-14 기준 104 tests 통과.
전체 suite 중 `test_keei.py`, `test_pubs_fetch.py`는 실행 환경에 `feedparser`가 없어 import 단계에서 제외했다. 이는 이번 변경과 무관하며 `requirements.txt`에는 이미 `feedparser>=6.0.0`가 선언돼 있다.
