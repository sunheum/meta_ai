# S2 개선 백로그

1. `major` — 짧은 약어의 과분해
   - 영향 예: `SCSSN`, `CRDIS`
   - 수정 위치: `app/segmentation.py`, S3 후보 선택 프롬프트
2. `major` — 다의 약어의 문맥 오선택
   - 영향 예: `TLM_OPNDT`의 `DT`
   - 수정 위치: `app/llm.py`, `app/prompts.py`
3. `major` — 219행 미해석
   - 수정 위치: S3 미지 약어 추론
4. `minor` — 완전 분해보다 낮은 커버리지의 대안 때문에 모호성이 과대 계산됨
   - 수정 위치: `app/workflow.py`
