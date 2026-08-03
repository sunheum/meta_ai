---
name: score-comment-korean-columns-ai
description: Independently review and score Korean attribute names generated from database column descriptions. Use for every meta_ai_kor_comment stage commit, regression candidate, or final quality gate that must verify source-row preservation, Korean naming policy, numeric preservation, semantic fidelity, contextual choices, frequency-based terminology consistency, and evidence calibration across the complete result population.
---

# AI 컬럼설명 기반 한글속성명 리뷰

생성 에이전트와 분리된 독립 평가자로 작업한다. 결과를 수정하거나 생성 프롬프트,
이전 AI 점수, 사람 점수, 생성 모델의 숨은 추론을 보지 않는다.

## 입력

- 원본 XLSX와 결과 XLSX
- stage ID와 commit SHA
- 실행 메타데이터와 용어 빈도 결정 파일

평가 전에 `references/naming-policy.md`와 `references/rubric.json`을 전부 읽는다.

## 전수 검사

1. 다음 명령으로 원본행·원본 컬럼·필수 결과 컬럼과 문자, 숫자, 상태, 근거,
   중복 일관성, 용어 빈도 결정을 결정적으로 검사한다.

```bash
python scripts/check_integrity.py --original source.xlsx --result result.xlsx \
  --terminology-decisions run-metadata.json --output deterministic-checks.json
```

2. 다음 명령으로 고유 `(컬럼명, 컬럼설명)` 리뷰 모집단을 만든다.

```bash
python scripts/build_review_population.py --original source.xlsx \
  --result result.xlsx --terminology-decisions run-metadata.json \
  --output review-population.jsonl
```

3. 모집단 전체를 의미 검토한다. 표본으로 축소하지 않는다. 각 행에서 원문 의미의
   누락·추가·반전, 영문 한글화, `/` 단일 의미 선택, 숫자 의미, 문맥 적합성,
   동의어 빈도 결정, 자연스러움과 상태·근거·신뢰도 정합성을 판단한다.
4. 모든 이슈에 `source_id`, `severity`, `description`, `evidence`,
   `recommendation`을 기록한다. 반복 원인은 `source_ids`로 묶을 수 있다.
5. 차원별 0~5 평점과 구체적인 전수 근거를 `ai-observations.json`에 작성한다.
   결정적 검사 실패 건수와 여덟 필수 체크를 그대로 포함한다.

## 독립 관찰 계약

`ai-observations.json`에 다음 필드를 모두 넣는다.

- `stage_id`, `commit_sha`
- `reviewed_item_count`, `expected_item_count`
- `deterministic_failure_count`
- `required_checks`: 루브릭에 정의된 여덟 boolean
- `dimensions`: 모든 차원의 `rating`과 비어 있지 않은 `evidence`
- `issues`: `critical`, `major`, `minor` 이슈 배열

`semantic_scope_complete`는 모집단 전체를 의미 검토한 경우에만 true로 둔다.
`score_scope_complete`는 전체 모집단과 결정적 검사를 모두 끝낸 경우에만 true로 둔다.

## 채점

```bash
python scripts/score_review.py --input ai-observations.json \
  --output ai-score.json
```

92점 이상, 모든 차원 4.0 이상, critical 0건, 결정적 실패 0건, 필수 체크 전부
true일 때만 통과한다. critical 또는 결정적 실패·필수 체크 실패가 있으면 원점수가
높아도 루브릭의 점수 상한을 적용한다.

## 산출물

- `deterministic-checks.json`
- `review-population.jsonl`
- `ai-observations.json`
- `ai-score.json`
- critical, major, 점수 영향도가 큰 minor 순의 개선 항목

새 commit은 이전 관찰과 점수를 복사하지 말고 원본 artifact부터 다시 평가한다.
