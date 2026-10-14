# 해외 AI 레인 — 0단계 gold

판정 기준을 고정하고 판정기를 재기 위한 산출물이다. production 상태 파일은 건드리지 않는다.
방향성 문서: [`docs/2026-10-14-overseas-ai-in-nuclear-tab-direction.md`](../../docs/2026-10-14-overseas-ai-in-nuclear-tab-direction.md)

![0단계 구성과 결과](../../docs/images/2026-10-14-ai-lane-stage0-overview.png)

## 파일

| 파일 | 내용 | 만드는 명령 |
|---|---|---|
| `candidates.jsonl` · `candidates_meta.json` | 아카이브 전량에 프리필터를 돌린 후보 1,814건과 층 | `candidates` |
| `sample.jsonl` · `sample_weights.json` | 층화 표본 226건과 층별 표집률(가중) | `sample` |
| `gold_labels.jsonl` | **gold 라벨 226건** — 이 디렉터리의 본체 | 사람(아래 지침) |
| `pred_baseline.jsonl` · `metrics_pred_baseline.json` | 규칙 기준선 판정과 점수 | `baseline` · `evaluate --pred` |
| `judge_requests_preview.jsonl` | production 판정기가 보낼 요청 29개(호출 안 함) | `judge --dry-run` |
| `pred_judge.jsonl` · `metrics_pred_judge.json` | production 판정기 결과 — **Gemini 키가 있는 곳에서 만든다** | `judge` · `evaluate --pred` |

```bash
python tools/ai_lane_stage0.py candidates
python tools/ai_lane_stage0.py sample
python tools/ai_lane_stage0.py baseline
python tools/ai_lane_stage0.py evaluate --pred eval_artifacts/ai_lane/pred_baseline.jsonl
GEMINI_API_KEY=... python tools/ai_lane_stage0.py judge          # 요청 29개, 하루 한도 500 중
python tools/ai_lane_stage0.py evaluate --pred eval_artifacts/ai_lane/pred_judge.jsonl
```

## 표본 설계

진짜 사례는 드물어서 표본으로 깎으면 정밀도·재현율이 몇 건에 흔들린다. 그래서 진짜 사례가 몰리는
층은 **전수**, 나머지는 해시 순위 표본이다. 해시 순위(`sha256(seed:id)` 작은 순)는 프리필터 어휘를
고쳐 풀에 항목이 더해져도 기존 선택을 거의 유지한다 — 표본이 통째로 바뀌어 gold 를 다시 매기는 일을 막는다.

| 층 | 표본 / 모집단 | |
|---|---|---|
| 해외 · 데이터센터 힌트 없음 | 149 / 149 | 전수 |
| 해외 · 데이터센터 힌트 | 20 / 210 | 표본 |
| 국내 | 25 / 526 | 표본 |
| 국내 · 제외 힌트 | 10 / 907 | 표본 |
| 지역 판정 없음 | 22 / 22 | 전수 |

`evaluate` 는 층별 가중(모집단 ÷ 표본)으로 90일 전체의 진짜 건수를 추정한다.

## 라벨 지침

`ai_lane.SYSTEM_PROMPT` 의 정의를 그대로 쓴다(사용자 결정 2026-10-14). 요약:

- **포함**: 한국 밖 원전 사업(설계·건설·운영·정비·검사·인허가·규제·해체·핵연료·안전조치·인력)에 AI 기술을
  적용·시범·계약·승인·발표한 기사. AI 기술이 주어·목적어이거나 성과의 핵심 수단이어야 한다.
  AI 판독 검사 로봇은 포함. 연구소 뉴스룸 기사는 포함. 해외 사례를 다룬 국내 매체 기사도 포함.
- **제외**: 데이터센터 전력 · 로봇·드론·VR(AI 가 수식어) · AI 산업 일반 · 외교 나열 · 국내 · 핵융합 ·
  논문 자체 · 군사 · AI 적용 아님 · 원전 아님.
- `confidence: low` 는 지침으로 가르기 어려웠던 경계 사례다. `note` 에 이유를 적었다.

**이 gold 는 Claude 가 지침에 따라 매겼다.** 처음 확신 낮음이던 20건은 2026-10-14 검토로 확정했다
(`note` 가 "검토 확정"으로 시작). 해설·기고·웨비나는 사용자 결정으로 포함한다.
`eval_artifacts/event_judgments_claude.jsonl` 과 같은 성격이다.

## 결과 (2026-10-14)

| 항목 | 값 |
|---|---|
| 아카이브 | 24,143건 · 90일(2026-07-17 ~ 10-14) |
| 프리필터 통과 | 1,814건 |
| gold | 226건 — 포함 25 · 제외 201 (경계 20건 검토 확정) |
| 90일 진짜 건수 추정 | 25건 → 주 1.9건 · 하루 0.28건 (지금 수집원 기준의 하한) |
| 그중 메인이 noise 로 버린 것 | 6 / 25 |
| 그중 아침 브리핑에 실린 것 | 6 / 25 |
| 규칙 기준선 | 정밀도 20% · 재현율 92% (포함 114건 중 91건 오탐) |
| production 판정기 | 미측정 — 이 세션 환경에 Gemini 키 없음 |

라벨링 중에 바뀐 것:

- 성숙도에 `discussion`(논의·해설)을 더했다. 해설·기고·웨비나처럼 주제는 맞지만 적용 사건이 없는 기사가
  담길 칸이 없었다. 포함할지는 열린 질문이다(gold 에선 포함·확신 낮음 5건).
- 프리필터에 `ML` 약어를 더했다. 영어 제목 「… with ML-based monitoring」이 안 걸리는 것을 테스트가 잡았다.
