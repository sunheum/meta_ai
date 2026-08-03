# 컬럼코멘트 없는 컬럼 한글화 에이전트

`data/table_column_template_컬럼코멘트N.xlsx`의 694개 영문 컬럼을
`result.xlsx` 매핑과 테이블 문맥으로 해석해 다음 값을 생성한다.

- 영문 Full Name
- 공백 없는 한글속성명
- 처리상태 (`자동확정`, `검토필요`, `검증실패`)
- 신뢰도
- 약어→Full Name→한글단어 변환근거

결과 생성은 사람 승인 없이 완료된다. 생성 후 개발 품질은 독립 AI 리뷰와 블라인드
사람 리뷰로 평가한다.

## 처리 흐름

1. 원본 12개 컬럼·694행을 읽고 문맥 키로 중복 생성을 축약한다.
2. 838개 매핑을 다의 약어를 보존한 인덱스로 로드한다.
3. 밑줄과 붙은 약어를 동적 계획법으로 분해한다.
4. 완전·단일 후보는 규칙으로 조합한다.
5. 미해석·다중 분해·다의 약어는 로컬 `Qwen3.6-27B-FP8`이 문맥으로 선택한다.
6. 결정적 검증 오류와 저신뢰 행만 최대 2회 LLM 리뷰한다.
7. `한글속성명_결과`와 `검토필요` 시트를 가진 XLSX를 생성한다.

`LLM_ENABLED=false` 또는 API의 `use_llm=false`로 실행하면 네트워크 없이
결정적 baseline을 만들 수 있다. 이때 미해석 의미는 `검증실패`로 표시되지만 결과
파일은 항상 생성된다.

## 설치

Python 3.11 이상이 필요하다.

```powershell
cd meta_ai_kor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

애플리케이션은 `.env` 파일을 자동 로드하지 않는다. 운영 환경에서 필요한 값을
환경변수로 주입하거나 `.env` 내용을 셸에 로드한다. 기본값은 기존
`meta_ai_mapping`의 로컬 LLM 설정과 같다.

주요 환경변수:

| 이름 | 기본값 | 설명 |
|---|---|---|
| `LLM_BASE_URL` | `http://192.168.100.91:8000/v1` | OpenAI 호환 로컬 엔드포인트 |
| `LLM_MODEL` | `Qwen3.6-27B-FP8` | 생성·리뷰 모델 |
| `LLM_ENABLE_THINKING` | `false` | JSON 변환 요청의 내부 추론 출력 비활성화 |
| `DEFAULT_BATCH_SIZE` | `25` | LLM 요청당 컬럼 수 |
| `DEFAULT_MAX_CONCURRENCY` | `10` | 동시 LLM 요청 |
| `DEFAULT_MAX_REVIEW_ROUNDS` | `2` | 오류행 리뷰 한도 |
| `AUTO_CONFIRM_THRESHOLD` | `85` | 자동확정 최소 신뢰도 |
| `MAPPING_WORKBOOK_PATH` | `result.xlsx` | 매핑 사전 |

## CLI 실행

전체 LLM 워크플로:

```powershell
python -m scripts.run_workflow `
  --input ..\data\table_column_template_컬럼코멘트N.xlsx `
  --mapping result.xlsx `
  --output results\korean_column_names.xlsx `
  --population results\review-population.jsonl `
  --metadata results\metadata.json
```

네트워크 없는 결정적 실행:

```powershell
python -m scripts.run_workflow `
  --input ..\data\table_column_template_컬럼코멘트N.xlsx `
  --mapping result.xlsx `
  --output results\deterministic.xlsx `
  --population results\review-population.jsonl `
  --no-llm
```

로컬 LLM 연결 확인:

```powershell
python -m scripts.smoke_llm
```

기존 결과의 `검증실패` 행만 재처리:

```powershell
python -m scripts.repair_failed `
  --source ..\data\table_column_template_컬럼코멘트N.xlsx `
  --result results\korean_column_names.xlsx `
  --mapping result.xlsx `
  --output results\korean_column_names_repaired.xlsx `
  --population results\review-population-repaired.jsonl `
  --metadata results\metadata-repaired.json
```

성능·메모리 기준선:

```powershell
python -m scripts.benchmark `
  --input ..\data\table_column_template_컬럼코멘트N.xlsx `
  --mapping result.xlsx `
  --output results\benchmark.json
```

## API 실행

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

엔드포인트:

- `GET /health`
- `POST /v1/korean-column-names`
- `POST /v1/korean-column-names/jobs`
- `GET /v1/korean-column-names/jobs/{job_id}`
- `GET /v1/korean-column-names/jobs/{job_id}/progress`
- `GET /v1/korean-column-names/jobs/{job_id}/result`

동기 요청:

```powershell
curl.exe -X POST http://localhost:8080/v1/korean-column-names `
  -F "file=@..\data\table_column_template_컬럼코멘트N.xlsx" `
  -F "batch_size=25" `
  -F "max_concurrency=10" `
  -F "max_review_rounds=2" `
  -o korean_column_names.xlsx
```

비동기 요청 후 응답의 `progress_url`을 스트리밍한다.

```powershell
curl.exe -X POST http://localhost:8080/v1/korean-column-names/jobs `
  -F "file=@..\data\table_column_template_컬럼코멘트N.xlsx"

curl.exe -N http://localhost:8080/v1/korean-column-names/jobs/{job_id}/progress
```

## 결과 XLSX

`한글속성명_결과` 시트는 원본 12개 컬럼 뒤에 다음 5개 컬럼을 추가한다.

1. `영문 Full Name`
2. `한글속성명`
3. `처리상태`
4. `신뢰도`
5. `변환근거`

`검토필요` 시트에는 `검토필요`와 `검증실패` 행만 복제하고 검토사유와 확인 포인트를
표시한다. 추론한 신규 매핑은 `result.xlsx`에 저장하거나 덮어쓰지 않는다.

## 리뷰 품질 게이트

stage 결과는 `quality/reviews/<stage>/<commit-sha>/`에 저장한다.

### AI 리뷰

`skills/score-korean-columns-ai/SKILL.md`와 전체 루브릭을 따라 독립 검토자가
`ai-observations.json`을 작성한 뒤 점수를 계산한다.

```powershell
python skills\score-korean-columns-ai\scripts\score_review.py `
  --input quality\reviews\S6\<sha>\ai-observations.json `
  --output quality\reviews\S6\<sha>\ai-score.json
```

최종 최소 기준은 90점, 모든 차원 3.5 이상, critical 0건이다.

### 사람 리뷰

먼저 stage ID 고정 seed로 AI 점수·신뢰도·생성 사유가 숨겨진 60행 표본을 만든다.

```powershell
python skills\score-korean-columns-human\scripts\select_sample.py `
  --input quality\reviews\S6\<sha>\review-population.jsonl `
  --stage-id S6 `
  --output quality\reviews\S6\<sha>\human-sample.csv
```

사람 리뷰어가 CSV의 5개 평점, `severity`, `comment`를 입력한 뒤 변환한다.

```powershell
python -m scripts.human_csv_to_ratings `
  --input quality\reviews\S6\<sha>\human-sample.csv `
  --stage-id S6 `
  --commit-sha <sha> `
  --reviewer-id reviewer-1 `
  --output quality\reviews\S6\<sha>\human-ratings.json

python skills\score-korean-columns-human\scripts\score_review.py `
  --input quality\reviews\S6\<sha>\human-ratings.json `
  --output quality\reviews\S6\<sha>\human-score.json
```

최종 최소 기준은 85점, 모든 차원 평균 3.5 이상, critical 0건, major 오류율
2% 이하이다. 실제 사람 평가가 없는 AI proxy 점수는 이 게이트를 대체하지 않는다.

## 테스트

```powershell
python -m pytest
```

테스트는 외부 LLM 없이 실행되며 입력·사전 계약, 분해, 정규화, 구조화 LLM 파싱,
오류행 리뷰, 원본 보존 XLSX, API, 비동기 작업과 heartbeat를 검증한다.
