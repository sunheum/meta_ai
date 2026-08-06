---
name: score-comment-korean-columns-human
description: Prepare and score a blinded stakeholder review of Korean attribute names generated from database column descriptions. Use for every meta_ai_kor_comment stage commit or final quality gate that needs a deterministic stage-seeded risk-stratified sample and genuine human judgments of business meaning, completeness, naming policy, contextual ambiguity, terminology consistency, and naturalness without exposing AI or generation signals.
---

# 사람 컬럼설명 기반 한글속성명 리뷰

실제 업무 이해관계자의 블라인드 평가를 준비하고 공식 점수만 계산한다. AI나 이
스킬을 실행하는 에이전트가 사람 평점을 생성·추정·대필하지 않는다.

## 입력

- AI 리뷰 스킬이 만든 `review-population.jsonl`
- stage ID와 commit SHA
- 실제 사람 리뷰어 식별자

먼저 `references/rubric.json`을 전부 읽는다.

## 고정 블라인드 표본

최초 candidate에서 다음 명령을 실행한다. CSV와 함께 선택 ID·위험군·quota를 담은
`human-sample.manifest.json`이 생성된다.

```bash
python scripts/select_sample.py --input review-population.jsonl \
  --stage-id S3 --output human-sample.csv
```

같은 stage의 개선 commit에서는 최초 manifest를 반드시 재사용한다.

```bash
python scripts/select_sample.py --input review-population.jsonl \
  --stage-id S3 --selection-manifest human-sample.manifest.json \
  --output human-sample.csv
```

CSV에는 `source_id`, 테이블명·설명, 컬럼명·설명, 생성 한글속성명만 보인다.
AI 점수·이슈, 위험군, 처리상태, 신뢰도, 생성근거, 이전 결과·점수는 숨긴다.

## 실제 사람 평가

여기서 실행을 멈추고 실제 사람이 CSV의 다음 여섯 차원에 1~5 정수를 입력하게 한다.

- `business_semantic_accuracy`
- `meaning_completeness`
- `character_notation_policy`
- `context_ambiguity_safety`
- `terminology_consistency`
- `naturalness_conciseness`

각 행의 `severity`에는 `pass`, `minor`, `major`, `critical` 중 하나를 입력한다.
`major`와 `critical`은 구체적인 `comment`가 필수다. 평점 의미는 1 사용할 수 없음,
2 중대한 수정 필요, 3 개선 필요, 4 대체로 적절, 5 그대로 표준명으로 사용 가능이다.

## 입력 검증과 채점

사람이 모든 행을 완료했다고 확인한 뒤에만 다음 명령을 실행한다.

```bash
python scripts/ratings_csv_to_json.py --input human-sample.csv \
  --manifest human-sample.manifest.json --stage-id S3 --commit-sha abc123 \
  --reviewer-id stakeholder-1 --attest-human-review \
  --output human-ratings.json
python scripts/score_review.py --input human-ratings.json \
  --output human-score.json
```

변환기는 누락·기본값·잘못된 평점, 표본 ID 변조, 사람 확인 누락을 거부한다. 채점기는
88점 이상, 모든 차원 평균 3.8 이상, critical 0건, major 오류율 2% 이하, 최소 80행과
모든 가용 위험군 quota 충족일 때만 통과시킨다.

## 독립성

- 사람이 CSV를 제출하기 전 AI 리뷰 결과를 보여주지 않는다.
- 에이전트는 사람 평점 셀이나 확인 플래그를 대신 채우지 않는다.
- 같은 stage에서 source ID는 고정하되 변경된 이름은 사람이 새로 평가한다.
- 결과 생성은 사람 평가를 기다리지 않는다. stage 완료 게이트만 사람 점수를 사용한다.

## 산출물

- `human-sample.csv`
- `human-sample.manifest.json`
- 사람이 작성한 CSV 원본
- `human-ratings.json`
- `human-score.json`
- major·critical 항목의 사람 수정 의견
