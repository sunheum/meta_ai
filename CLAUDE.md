# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

영문 컬럼명을 한글 속성명으로 변환하는 3단계 파이프라인. 각 단계는 독립된 FastAPI 서비스로 분리되어 있으며, 앞 단계의 산출물이 뒷 단계의 입력이 된다.

| 순서 | 프로젝트 | 입력 | 산출물 | 포트 |
|---|---|---|---|---|
| 1 | `meta_ai_kor_comment/` | `data/table_column_template_컬럼코멘트Y.xlsx` (컬럼설명 존재) | 컬럼설명 기반 한글속성명 XLSX | 8003 |
| 2 | `meta_ai_mapping/` | `data/table_column_template_컬럼코멘트Y.xlsx` | `영문약어 · Full Name · 한글단어` 매핑 사전 (`result.xlsx`) | 8002 |
| 3 | `meta_ai_kor/` | `data/table_column_template_컬럼코멘트N.xlsx` + `meta_ai_kor/result.xlsx` (2단계 산출) | 컬럼코멘트 없는 컬럼의 한글속성명 XLSX | 8080 |

2단계의 `result.xlsx`는 3단계의 매핑 사전으로 재사용된다. 즉 `meta_ai_mapping` 개선은 `meta_ai_kor`의 baseline 품질에 직접 영향을 준다.

## Shared context

- **입력 XLSX는 `data/`에 공용으로 위치**하며 어떤 프로젝트도 원본을 수정하지 않는다. 서브 프로젝트 CLI는 `..\data\...` 상대 경로로 참조한다.
- **로컬 LLM은 3개 프로젝트가 동일 엔드포인트를 공유**한다. 기본값 `LLM_BASE_URL=http://192.168.100.91:8000/v1`, `LLM_MODEL=Qwen3.6-27B-FP8`. 연결 timeout 15초, 응답 read timeout 1800초(30분)로 고정된 규약을 지킨다.
- **애플리케이션이 `.env`를 자동 로드하지 않는다.** 셸에서 환경변수를 미리 export/set 해야 하고, `.env.example`는 참고용이다.
- 각 프로젝트는 독립 가상환경(`.venv`)과 독립 `pyproject.toml`을 갖는다. 루트에는 공용 dependency나 build 파일이 없다.

## Cross-project conventions

- **비동기 작업 API 규약**: `POST /v1/...jobs` → `job_id` 발급 → `GET /v1/...jobs/{job_id}/progress`를 `curl -N`으로 스트리밍. 진행률·단계 누적시간·전체 소요시간·중간 건수를 실시간 출력하며, `PROGRESS_HEARTBEAT_SECONDS`(기본 15초) 간격으로 `연결 유지` 하트비트를 보낸다. 프록시/클라이언트의 유휴 연결 timeout 방지가 목적이므로 임의로 비활성화하지 않는다.
- **결과 XLSX 규약**: 원본 컬럼과 행 순서를 그대로 보존한 뒤 뒤에 결과 컬럼을 추가. `한글속성명_결과` 시트와 `검토필요` 시트(=`검토필요`+`검증실패` 행 복제)로 분리한다. 결과 파일은 임시 파일에 완성한 뒤 최종 경로로 원자적 교체한다.
- **처리상태 3단계**: `자동확정` / `검토필요` / `검증실패`. `AUTO_CONFIRM_THRESHOLD` 기본 85점(2단계는 90) 이상만 자동확정.
- **결정적 검증 → 제한된 LLM 리뷰 루프**: `DEFAULT_MAX_REVIEW_ROUNDS`(기본 2)회로 리뷰 반복을 제한한다. 리뷰 뒤에도 남은 실패는 작업을 실패시키지 않고 부분 결과에 `검증실패`로 기록한다.
- **입력 중복 축약**: 컬럼명+설명(또는 문맥 키)이 동일하면 한 번만 생성한 뒤 원본 행에 복제. 원본 행 수는 항상 보존된다.
- **도메인 규칙은 YAML로 주입**: 영문 약어 사전과 동의어 그룹은 코드가 아닌 프로젝트별 YAML 규칙 파일로 전달한다. `meta_ai_kor_comment`에서는 `--rules PATH` CLI 옵션 또는 `RULES_PATH` env로 지정하고, 파일이 없으면 도메인 무관 순수 LLM 경로로 동작한다. 보험/자동차 preset은 `meta_ai_kor_comment/config/rules/examples/insurance.yaml`.

## Development workflow

- Python 3.11+ 필요. 각 프로젝트 디렉터리 안에서 `python -m venv .venv` → `pip install -e ".[dev]"` → 해당 프로젝트의 uvicorn 명령을 실행한다.
- 테스트는 각 프로젝트에서 `pytest`. 모든 테스트는 외부 LLM을 호출하지 않고 가짜 모델/실입력 파일로 계약을 검증한다.
- 로컬 LLM 연결 smoke test는 각 프로젝트의 `scripts/smoke_llm.py`.
- 데이터가 실제로 처리되는지 확인하는 end-to-end 실행은 CLI 스크립트나 실제 API 호출을 사용하되, 커밋에 `data/` 원본이 변경되지 않도록 주의한다(`.gitignore`에 등록됨).

## Quality gate (meta_ai_kor 및 meta_ai_kor_comment)

두 프로젝트는 stage commit 단위로 재현 가능한 AI·사람 리뷰 점수를 남긴다.

- 산출물 경로: `<project>/quality/reviews/<stage>/<commit-sha>/`
- 파일 구성: `ai-observations.json`, `ai-score.json`, `human-sample.csv`, `human-ratings.json`, `human-score.json`, `improvement-backlog.md`
- 최소 기준: AI 리뷰 90점(모든 차원 3.5 이상, critical 0), 사람 리뷰 85점(critical 0, major 오류율 2% 이하)
- 사람 평점은 실제 이해관계자가 입력해야 하며, AI proxy 점수로 대체할 수 없다.

## Working notes

- 각 프로젝트 하위 `CLAUDE.md`에 서비스별 상세(명령, 아키텍처 계층, 환경변수, 실패 모드)가 있으므로 해당 프로젝트에서 작업할 때는 그 파일을 우선 참조한다.
- README는 한국어가 정본이며, 새 기능 추가 시 README와 하위 `CLAUDE.md`를 함께 갱신한다.
