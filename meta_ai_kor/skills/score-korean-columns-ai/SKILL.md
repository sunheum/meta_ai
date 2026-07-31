---
name: score-korean-columns-ai
description: Score Korean database column-name conversion results with deterministic integrity checks and an independent semantic AI review. Use for every meta_ai_kor stage commit, regression candidate, or final XLSX quality gate that must evaluate English Full Names, Korean attribute names, mapping evidence, ambiguity handling, and cross-row consistency.
---

# AI 한글속성명 리뷰

생성 에이전트와 분리된 평가자로 작업한다. 결과를 수정하지 말고 정확한 원본행 근거와
재현 가능한 100점 점수를 남긴다.

## 필수 입력

- 원본 XLSX
- 매핑 XLSX
- 평가할 결과 XLSX
- stage ID와 commit SHA
- 가능하면 실행 메타데이터와 검증 로그

`references/rubric.json`을 전부 읽고 그 버전과 배점을 그대로 사용한다.

## 평가 절차

1. 원본과 결과의 행 수, 행 순서, 원본 12개 컬럼, 필수 결과 컬럼을 전수 비교한다.
2. 694개 전체 결과에 빈 값, 한글속성명 형식, 신뢰도 범위, 변환근거 일치를 검사한다.
3. 문맥 키 `(컬럼명, 테이블명, 테이블설명, 데이터타입)`별 고유 결과를 전부 의미
   검토한다. 표본으로 줄이지 않는다.
4. 매핑 사전과 변환근거를 대조해 약어 분해, Full Name, 한글단어의 출처를 확인한다.
5. 다의 약어, 미해석 추론, `검토필요`, 중복 컬럼의 다른 문맥을 우선 재검토한다.
6. 생성 프롬프트의 설명, 이전 리뷰 점수, 사람 리뷰 결과를 보지 않은 독립 판단을 한다.
7. 각 이슈에 `source_id`, 심각도, 기대값, 실제값, 근거, 수정 제안을 기록한다.
8. 차원별 0~5 평점과 근거를 `ai-observations.json`으로 작성한다.
9. 다음 명령으로 공식 점수를 계산한다.

```bash
python scripts/score_review.py \
  --input ai-observations.json \
  --output ai-score.json
```

## 심각도

- `critical`: 원본행 누락·변조, 빈 결과, 핵심 의미 반전, 근거 없는 업무 개념 추가
- `major`: 중요한 의미 누락, 잘못된 Full Name, 문맥상 잘못된 다의어 선택
- `minor`: 자연스러움, 표준어, 추적성의 국소 문제

동일 원인의 반복 문제는 영향 행 수를 함께 기록하고 대표 이슈 하나로 묶을 수 있다.
점수를 맞추기 위해 근거 없는 감점을 만들지 않는다.

## 필수 확인

`ai-observations.json`의 `required_checks`에 다음 키를 모두 넣는다.

- `row_preservation`
- `required_columns_complete`
- `korean_name_format`
- `evidence_traceable`
- `score_scope_complete`

하나라도 거짓이면 통과시킬 수 없다.

## 산출물

- `ai-observations.json`
- `ai-score.json`
- 우선순위가 정렬된 개선 항목

점수가 기준 미달이면 critical, major, 점수 영향도가 큰 minor 순으로 수정 대상을
제안한다. 새 commit을 평가할 때 이전 점수를 복사하지 말고 원본부터 다시 검토한다.

