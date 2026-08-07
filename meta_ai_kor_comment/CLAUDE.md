# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role in the pipeline

파이프라인 1단계. **컬럼설명이 존재하는 컬럼**에 대해 컬럼설명을 주 근거로 공백 없는 한글속성명을 생성한다. `ID` 외 영문은 한글화하고, 숫자는 원래 순서·의미대로 보존한다. 이 서비스는 도메인 무관 범용 파이프라인으로, 도메인 특화 지식(영문 약어 사전, 동의어 그룹)은 코드가 아닌 YAML 규칙 파일로 주입한다. 보험/자동차 도메인 규칙은 `config/rules/examples/insurance.yaml`에 preset으로 제공된다.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# API 기동 (포트 8003 고정)
uvicorn app.main:app --host 0.0.0.0 --port 8003

# 로컬 LLM 연결 smoke
python scripts/smoke_llm.py

# 전체 테스트
pytest

# 단일 테스트
pytest tests/test_workflow_actual_data.py::test_name -q
```

동기 요청:

```bash
curl -X POST "http://localhost:8003/v1/comment-korean-column-names" \
  -F "file=@../data/table_column_template_컬럼코멘트Y.xlsx" \
  -F "batch_size=25" -F "max_concurrency=10" \
  -F "max_review_rounds=2" -F "auto_confirm_threshold=90" \
  -o 한글속성명.xlsx
```

비동기 진행률 스트리밍:

```bash
curl -X POST "http://localhost:8003/v1/comment-korean-column-names/jobs" \
  -F "file=@../data/table_column_template_컬럼코멘트Y.xlsx"
curl -N "http://localhost:8003/v1/comment-korean-column-names/jobs/{job_id}/progress"
```

## Architecture

`app/` 모듈은 하나의 워크플로 파이프라인으로 조립된다:

```
excel(원본 보존·중복 축약)
  → normalization(정규화·위험 분류)
  → workflow(보수 생성)
  → terminology(빈도 기반 용어 통일)
  → validation(결정적 검증)
  → llm(오류행만 제한된 리뷰) → terminology (재통일 루프)
  → workflow(상태·신뢰도 산정)
  → excel(한글속성명_결과 + 검토필요 시트로 원자적 쓰기)
```

- **중복 축약**: 컬럼명+설명이 같은 행은 한 번만 생성한 뒤 원본 행에 복제. 결과 파일은 원본 1,195행 순서를 그대로 유지한다.
- **결정적 검증 우선**: LLM 리뷰는 결정적 검증에서 실패한 행에 대해서만 최대 `DEFAULT_MAX_REVIEW_ROUNDS`회(기본 2) 재실행한다.
- **`검증실패` vs `검토필요` 구분**: LLM 호출·응답 실행 실패로 남은 행은 `검증실패`. 이미 결정적으로 유효한 현재 결과를 보호하기 위해 대체 리뷰 후보가 거부된 경우에는 현재 값을 유지한 `검토필요`로 남긴다. 두 카운트는 별도 통계로 기록된다.
- **결과 시트 확장 컬럼**: 원본 12개 열 뒤에 `한글속성명`, `처리상태`, `신뢰도`, `처리방식`, `변환근거`, `검토사유` 순으로 추가한다.
- **원자적 쓰기**: 결과 XLSX는 임시 파일에 완성한 후 최종 경로로 rename한다.

## Environment variables

`.env`는 자동 로드되지 않는다. 셸에서 export/set 필요. 핵심 값:

| 변수 | 기본값 | 의미 |
|---|---|---|
| `LLM_BASE_URL` | `http://192.168.100.91:8000/v1` | OpenAI 호환 로컬 엔드포인트 |
| `LLM_MODEL` | `Qwen3.6-27B-FP8` | 생성·리뷰 모델 |
| `LLM_TRUST_ENV` | `false` | httpx가 시스템 프록시를 신뢰할지 여부 |
| `LLM_CONNECT_TIMEOUT_SECONDS` / `LLM_READ_TIMEOUT_SECONDS` | `15` / `1800` | 연결·응답 timeout(초). 응답은 30분까지 대기 |
| `DEFAULT_BATCH_SIZE` / `DEFAULT_MAX_CONCURRENCY` | `25` / `10` | 배치당 컬럼 수 · 동시 LLM 요청 |
| `DEFAULT_MAX_REVIEW_ROUNDS` | `2` | 오류행 리뷰 반복 상한 |
| `DEFAULT_AUTO_CONFIRM_THRESHOLD` | `90` | 자동확정 최소 신뢰도 |
| `PROGRESS_HEARTBEAT_SECONDS` | `15` | 진행률 스트림 하트비트 간격 |
| `INPUT_SHEET_NAME` / `RESULT_SHEET_NAME` / `REVIEW_SHEET_NAME` | `테이블_컬럼_정보` / `한글속성명_결과` / `검토필요` | 시트 이름 규약 |
| `RULES_PATH` | `config/rules.yaml` | 도메인 규칙 YAML 경로. 파일이 없으면 빈 규칙셋으로 도메인 무관 동작. |

## Domain rules (YAML)

도메인 지식은 코드 밖 YAML 규칙 파일로 주입한다. 스키마:

```yaml
glossary:
  - source: FY          # 대소문자 무시(대문자로 정규화)
    target: 회계        # 영문 약어의 한글 표준어
    note: 회계연도 약어  # 선택. 감사/리뷰 힌트
synonym_groups:
  - id: payment-action  # 그룹 고유 ID
    candidates: [납입, 납부]  # 첫 후보가 빈도 동률 시 우선 채택
```

로드 순서:

1. CLI: `python scripts/run_actual.py <입력> <출력> --rules path/to/rules.yaml`
2. env: `RULES_PATH=path/to/rules.yaml`
3. 기본: `config/rules.yaml` (없으면 빈 규칙셋)

**빈 규칙셋(default)** 에서는 도메인 하드코딩 없이 순수 LLM + 문자 정책·숫자 보존만 적용한다. 보험/자동차 도메인을 처리하려면 `--rules config/rules/examples/insurance.yaml` 또는 `RULES_PATH=config/rules/examples/insurance.yaml`를 지정한다.

### Bootstrap workflow (새 도메인 데이터)

1. `python scripts/build_rules_template.py <입력.xlsx> config/rules.yaml`
   — 입력의 미확정 영문 토큰을 정적 추출해서 `source`만 채운 템플릿을 생성. LLM 미필요.
   `python scripts/run_actual.py ... --emit-rules-template PATH`로 파이프라인 실행과
   동시에 방출도 가능.
2. 생성된 파일의 `target`을 채우고 `--rules`로 지정해 재실행.
3. 결과 XLSX의 `검토필요` 시트에서 "용어 통일" 리뷰 사유를 보고 `synonym_groups`를
   증분 추가한다. `candidates` 첫 항목이 빈도 동률 시 우선 채택됨을 활용한다.
4. `--extend config/rules.yaml`로 이미 채운 토큰을 제외한 새 토큰만 추출 가능.

## Quality gate

stage 결과는 `quality/reviews/<stage>/<commit-sha>/`에 저장한다. `skills/score-comment-korean-columns-ai`와 `skills/score-comment-korean-columns-human`을 사용해 각각 AI 리뷰(전수 구조·정책·의미 충실도)와 사람 리뷰(고정 위험도 표본, 블라인드)를 실행한다. 사람 평점은 실제 이해관계자가 입력하며 AI가 대신 작성하지 않는다.

## Testing notes

- 모든 테스트는 외부 LLM을 호출하지 않고 실제 입력 워크북(`../data/...`)을 기반으로 원본 1,195행 보존, 결과 열, 검토 시트, API·작업 진행률 계약을 검증한다.
- `test_review_loop_actual_data.py`, `test_workflow_actual_data.py`, `test_api_actual_data.py`가 실 데이터 기반 계약 테스트다.
