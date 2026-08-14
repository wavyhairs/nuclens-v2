# Nuclens 2차 통합 설계 — story 계약·장기 흐름·이중 오디오

## 목표

뉴스 선정 로직과 웹 표시 로직이 서로 다른 '같은 사건' 정의를 사용하지 않게 한다. Daily Brief가 만든 briefing story를 공통 데이터 계약으로 삼고, Nuclens는 그 story를 날짜를 넘어선 issue로 연결한다. 동시에 장기 흐름과 두 종류 오디오를 하나의 Pages 배포에 포함한다.

## 1. 데이터 계층

1. **article**: 수집된 개별 기사/공식자료.
2. **briefing story**: 같은 underlying event를 다룬 기사들을 Daily Brief에서 병합한 단위. `duplicate / merge / single` 관계, fingerprint, article/outlet/tier1/independent counts, 관련 제목·근거를 보존한다.
3. **issue**: 여러 날짜의 story 가운데 후속 보도를 연결하는 장기 추적 단위. 새 승인·계약·재가동 등 독립 action은 별도 story로 유지하되 같은 장기 issue의 timeline에 연결할 수 있다.

`delivery_log.jsonl`은 선정된 모든 story의 계약을 기록한다. 다중보도 story만 기록하던 조건은 제거했다. 이를 통해 단독 공식발표의 fingerprint도 웹 issue clustering이 사용할 수 있다.

## 2. 웹 issue 연결

기존 title/token/tag/embedding 기준을 유지하되 `story_fingerprint_similarity`를 추가한다. 국가·주체·시설·event family·action·cause 축을 비교하며, 충분한 구체축이 일치하는 경우에만 자동 연결한다. 회색지대는 `issue_review.py`가 두 fingerprint까지 보고 검수한다.

대표 기사 하나의 표현 때문에 같은 사건이 갈라지지 않도록 story에 병합된 관련 제목/요약도 lexical 보조 신호에 포함한다. 반대로 국가/호기/시설 충돌 veto는 기존처럼 우선한다.

## 3. 장기 트렌드

`trend.json.periods`에 7/30/90/180/365일 집계를 생성한다. 단위는 원문 기사 언급 수가 아니라 `briefing_story`다. 따라서 같은 사건이 5개 매체에서 보도돼도 트렌드에는 story 1건으로 들어가며 `multi_source_story_count`에서 다중보도 신호를 별도로 확인한다.

- 7일: 일별 흐름 + 전주 story 비교
- 30/90일: ISO 주차별 흐름
- 180/365일: 월별 흐름
- 각 bucket: story 수, 복수매체 story 수, must-read 수, 상위 주제, 대표 사건 2건

archive 축적기간이 요청기간보다 짧으면 `complete_period=false`, `available_days`, `requested_start`, 실제 `start`를 함께 제공한다. UI는 “1년”을 선택해도 현재 30일만 있다면 이를 명시한다.

`story_contract_coverage`도 함께 제공한다. v2 적용 이전의 과거 `delivery_log`에는 fingerprint/매체 통합정보가 없으므로 이를 “복수매체 0건”으로 오인하지 않고, UI에서 “story-v2 적용률”로 구분한다. 신규 선정분부터 적용률이 누적된다.

## 4. 오디오 계약 v2

`web/public/data/audio/audio.json`:

```json
{
  "date": "2026-08-15",
  "default_variant": "fast",
  "variants": {
    "fast": {"file": "briefing-fast-....mp3", "duration_sec": 180},
    "expert": {"file": "briefing-expert-....mp3", "duration_sec": 600}
  }
}
```

### 빠른 브리핑

기존 nuclear 방식 유지. 1인 라디오형 약 3분. 900자 청크, silent truncation 차단, head/tail silence trim, 450ms gap, `dynaudnorm → -16 LUFS`, 96kbps.

### 전문가 브리핑

개선 NucBrief 알고리즘을 기존 `gemini_client` 위에 이식한다.

`선정 issue → evidence dossier(정책·사업 + 기술·운영 렌즈) → deterministic 시간배분 → EpisodePlan → 수석 원자력 분석가 1인 대본 → 독립검증 → 필요 시 1회 수정 → 검증 재통과 → TTS`

TTS는 900자/Kore/450ms/-16LUFS/128kbps. 잘린 청크는 동일 모델에서 재생성하고, 모델 전환이 초반이면 전체 재생성해 음색을 맞추며 후반이면 정상 구간을 보존해 완주한다. 중대한 unsupported claim이 남으면 전문가 음원을 배포하지 않는다.

## 5. 실패 격리

빠른 음원, 전문가 음원, Pages 배포는 서로 독립적이다. 전문가 생성이 quota/검증/TTS 문제로 실패해도 빠른 브리핑과 사이트는 배포된다. `force_audio` 수동 실행 시 두 variant를 모두 재생성하되 빠른 브리핑은 `--no-send`로 텔레그램 중복발송을 막는다.

## 6. 저장·용량

장기 archive 전체를 Pages JSON에 싣지 않는다. 상세 news/issue 화면은 기존 짧은 window를 유지하고, 분기·반기·연간 화면에는 선정 story를 집계한 경량 `periods`만 전송한다. 따라서 장기 이력 기능을 추가해도 브라우저 payload와 Cloudflare 정적 호스팅 비용이 급증하지 않는다.
