---
name: score-korean-columns-human
description: Prepare and score a blinded human review of Korean database column-name conversion results using a deterministic risk-stratified sample. Use for every meta_ai_kor stage commit or final quality gate that needs an independent human judgment of business meaning, completeness, Korean naturalness, terminology consistency, and ambiguity risk.
---

# 사람 한글속성명 리뷰

AI 점수에 영향을 받지 않는 블라인드 사람 평가를 준비하고 공식 점수를 계산한다.
이 스킬은 결과 생성 승인이 아니라 개발 stage의 품질 게이트에 사용한다.

## 필수 입력

- 리뷰 모집단 JSON 또는 JSONL
- stage ID와 평가할 commit SHA
- 사람 리뷰어 식별자

모집단 각 항목은 최소한 `source_id`, `review_stratum`, `컬럼명`, `테이블명`,
`테이블설명`, `영문 Full Name`, `한글속성명`을 포함한다.
`references/rubric.json`을 전부 읽고 배점과 표본 규칙을 그대로 사용한다.

## 블라인드 표본 생성

stage ID를 seed로 사용해 다음 명령을 실행한다.

```bash
python scripts/select_sample.py \
  --input review-population.jsonl \
  --stage-id S3 \
  --output human-sample.csv
```

같은 stage의 개선 commit에서는 같은 표본을 유지한다. 표본 파일에서 다음 항목을
숨긴다.

- AI 점수와 AI 이슈
- 생성 신뢰도와 처리상태
- 이전 stage 결과와 사람 점수
- 생성 모델의 추론 설명

## 사람 평가

각 표본행을 독립적으로 읽고 다음 5개 차원에 1~5점을 입력한다.

- `business_semantic_accuracy`
- `meaning_completeness`
- `korean_naturalness`
- `terminology_consistency`
- `business_ambiguity_safety`

또한 `severity`에 `pass`, `minor`, `major`, `critical` 중 하나를 입력하고,
`major` 또는 `critical`이면 구체적인 수정 의견을 필수로 남긴다.

평점 기준:

- 1: 사용할 수 없음
- 2: 중대한 수정 필요
- 3: 의미는 전달되나 개선 필요
- 4: 대체로 적절함
- 5: 그대로 표준명으로 사용 가능

critical은 핵심 의미 반전, 다른 업무 개념 생성, 원본과 관계없는 이름처럼 실제
사용 시 오해를 일으키는 경우에만 사용한다.

## 점수 계산

작성된 평가를 다음 형태의 JSON으로 변환한다.

```json
{
  "stage_id": "S3",
  "commit_sha": "abc123",
  "reviewer_id": "reviewer-1",
  "rows": [
    {
      "source_id": "row-2",
      "review_stratum": "mapping_ambiguity",
      "ratings": {
        "business_semantic_accuracy": 5,
        "meaning_completeness": 4,
        "korean_naturalness": 4,
        "terminology_consistency": 5,
        "business_ambiguity_safety": 5
      },
      "severity": "pass",
      "comment": ""
    }
  ]
}
```

다음 명령으로 공식 점수를 계산한다.

```bash
python scripts/score_review.py \
  --input human-ratings.json \
  --output human-score.json
```

## 독립성 규칙

- AI 평가 결과를 보기 전에 사람 평가를 완료한다.
- 개선 commit을 평가할 때 기존 의견은 볼 수 있지만 기존 점수를 그대로 복사하지 않는다.
- 한 사람이 모든 stage를 검토할 수 있으나, 최종 S6은 가능하면 두 번째 독립
  리뷰어의 교차 검토를 추가한다.
- 결과 생성은 사람 리뷰를 기다리지 않는다. stage 완료 표시만 사람 점수 게이트를 따른다.

## 산출물

- `human-sample.csv`
- `human-ratings.json`
- `human-score.json`
- major·critical 항목의 수정 의견

