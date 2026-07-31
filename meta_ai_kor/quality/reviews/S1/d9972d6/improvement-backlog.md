# S1 개선 백로그

1. `major` — 373행에 `미정` 의미가 남음
   - 원인: 밑줄 토큰 전체가 사전에 없으면 내부 약어를 분해하지 않음
   - 수정 위치: `app/segmentation.py`
   - 회귀 테스트: `DTHMS → DT + HMS`, `ORGCD → ORG + CD`
2. `major` — 187행의 다의 약어가 출현건수·단순 문맥 점수에 의존
   - 수정 위치: S3 LLM 후보 선택
3. `minor` — `일자+시각` 등 표준 결합 규칙이 없음
   - 수정 위치: `app/normalization.py`
