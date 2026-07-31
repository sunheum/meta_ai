# S0 개선 백로그

1. `critical` — 694행 결과가 아직 없음
   - 수정 위치: `app/excel.py`, `app/glossary.py`
   - 다음 단계: S1에서 원본 보존 입출력과 매핑 사전 인덱스를 구현한다.
2. `major` — 분해·Full Name·한글속성명 생성이 없음
   - 수정 위치: S2 분해·정규화 엔진
3. `major` — 문맥 다의성 해소가 없음
   - 수정 위치: S3 LLM 선택·추론
