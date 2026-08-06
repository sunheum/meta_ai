# S5 개선 백로그 — precommit

## 평가 범위와 제한

- 허용 근거: `review-population.jsonl`, `deterministic-checks.json`
- 전수 범위: 921개 고유 조합, 1,195개 발생
- 결정 검사 기록: 실패 0건
- 증거 제한: `result.xlsx`, 원본 XLSX, `run-metadata.json`이 없어 원시 행 대조와 실행·용어 결정 이력을 독립 재검증할 수 없다.
- 필수 체크 실패: `character_policy_complete`, `terminology_frequency_verified`, `evidence_traceable`, `score_scope_complete`
- 점수는 필수 체크 실패 상한 59점을 적용한다.

## Major

1. `row-107`: RH를 업무 의미로 해석하지 않고 `알에이치형태코드`로 음차한 뒤 자동확정했다. 약어 사전 확인 전에는 검토필요로 둔다.
2. `row-190`: SOFA를 `주한미군`으로 축약해 협정 적용 범위를 누락했다. `주한미군지위협정적용차량여부`처럼 범위를 명시한다.
3. `row-838`, `row-984`, `row-985`: 특약율 2 대 특약요율 1의 빈도 그룹이 누락됐다. 최빈형 `특약율`로 통일하고 결정 이력을 남긴다.

## Minor

1. `row-250`: 보험기간 순번을 `2회차보험기간적용보험료`로 정규화한다.
2. `row-623`: 중복어를 제거해 `한가족우대등급코드`로 정규화한다.
3. `row-174`, `row-178`, `row-869`, `row-875`: 검토필요와 신뢰도 100의 모순을 해소하고 변경 근거 문구를 정정한다.
4. `row-794`, `row-795`, `row-1146`: WKDY·SP 의미 확인 전에는 검토필요로 전환한다.

## 증거 복구 후 재검증

`result.xlsx`, 원본 XLSX, `run-metadata.json`을 복구한 뒤 행·컬럼 무결성, 전체 용어 그룹, 실행 이력과 결과 파일 해시를 다시 검증한다.
