from __future__ import annotations

import csv
import importlib.util
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
AI_SCRIPTS = PROJECT / "skills" / "score-comment-korean-columns-ai" / "scripts"
HUMAN_SCRIPTS = PROJECT / "skills" / "score-comment-korean-columns-human" / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(AI_SCRIPTS))
ai_population = load_module(
    "build_review_population", AI_SCRIPTS / "build_review_population.py"
)
ai_integrity = load_module("ai_integrity", AI_SCRIPTS / "check_integrity.py")
ai_score = load_module("ai_score", AI_SCRIPTS / "score_review.py")
human_select = load_module("human_select", HUMAN_SCRIPTS / "select_sample.py")
human_convert = load_module(
    "human_convert", HUMAN_SCRIPTS / "ratings_csv_to_json.py"
)
human_score = load_module("human_score", HUMAN_SCRIPTS / "score_review.py")


AI_RUBRIC = json.loads(
    (PROJECT / "skills/score-comment-korean-columns-ai/references/rubric.json").read_text(
        encoding="utf-8"
    )
)
HUMAN_RUBRIC = json.loads(
    (
        PROJECT
        / "skills/score-comment-korean-columns-human/references/rubric.json"
    ).read_text(encoding="utf-8")
)


def perfect_ai_review() -> dict:
    return {
        "stage_id": "S4",
        "commit_sha": "abc123",
        "reviewed_item_count": 921,
        "expected_item_count": 921,
        "deterministic_failure_count": 0,
        "required_checks": {
            name: True for name in AI_RUBRIC["required_checks"]
        },
        "dimensions": {
            item["id"]: {"rating": 5, "evidence": ["전수 검토 완료"]}
            for item in AI_RUBRIC["dimensions"]
        },
        "issues": [],
    }


def review_population(size: int = 160) -> list[dict]:
    strata = (
        ["low_risk_keep"] * 20
        + ["english_translation"] * 20
        + ["numeric_preservation"] * 20
        + ["slash_context"] * 10
        + ["terminology_frequency"] * 20
        + ["duplicate_context"] * 20
        + ["llm_rewrite"] * 20
        + ["review_needed"] * 20
        + ["other"] * 10
    )
    return [
        {
            "source_id": f"row-{index + 2}",
            "테이블명": f"테이블{index % 5}",
            "테이블설명": "계약",
            "컬럼명": f"COL_{index}",
            "컬럼설명": f"설명{index}",
            "한글속성명": f"속성{index}",
            "처리상태": "자동확정",
            "신뢰도": 99,
            "변환근거": "숨김",
            "review_strata": [strata[index]],
            "ai_score": 100,
        }
        for index in range(min(size, len(strata)))
    ]


def perfect_human_review(manifest: dict) -> dict:
    dimensions = [item["id"] for item in HUMAN_RUBRIC["dimensions"]]
    return {
        "stage_id": manifest["stage_id"],
        "commit_sha": "abc123",
        "reviewer_id": "stakeholder-1",
        "review_provenance": {
            "rating_source": "human_entered",
            "reviewer_attested": True,
            "ai_generated": False,
        },
        "sample_manifest": manifest,
        "rows": [
            {
                "source_id": source_id,
                "ratings": {dimension: 5 for dimension in dimensions},
                "severity": "pass",
                "comment": "",
            }
            for source_id in manifest["source_ids"]
        ],
    }


class AiReviewSkillTests(unittest.TestCase):
    def test_rubric_weights_and_perfect_gate(self) -> None:
        self.assertEqual(sum(item["weight"] for item in AI_RUBRIC["dimensions"]), 100)
        result = ai_score.score(AI_RUBRIC, perfect_ai_review())
        self.assertEqual(result["score"], 100)
        self.assertTrue(result["passed"])

    def test_critical_and_deterministic_failures_enforce_caps(self) -> None:
        critical = perfect_ai_review()
        critical["issues"] = [
            {
                "source_id": "row-2",
                "severity": "critical",
                "description": "핵심 의미 반전",
                "evidence": "원문과 결과가 반대",
                "recommendation": "원문 의미 복원",
            }
        ]
        critical_result = ai_score.score(AI_RUBRIC, critical)
        self.assertEqual(critical_result["score"], 69)
        self.assertFalse(critical_result["passed"])

        deterministic = perfect_ai_review()
        deterministic["deterministic_failure_count"] = 1
        deterministic["required_checks"]["numeric_preservation_complete"] = False
        deterministic_result = ai_score.score(AI_RUBRIC, deterministic)
        self.assertEqual(deterministic_result["score"], 59)
        self.assertFalse(deterministic_result["passed"])

    def test_missing_dimension_and_evidence_are_rejected(self) -> None:
        review = perfect_ai_review()
        review["dimensions"].pop("korean_name_naturalness")
        with self.assertRaises(ValueError):
            ai_score.score(AI_RUBRIC, review)
        review = perfect_ai_review()
        review["dimensions"]["korean_name_naturalness"]["evidence"] = []
        with self.assertRaises(ValueError):
            ai_score.score(AI_RUBRIC, review)

    def test_integrity_checks_policy_and_frequency(self) -> None:
        original_headers = [
            "스키마",
            "테이블명",
            "테이블설명",
            "컬럼명",
            "컬럼설명",
            "데이터타입",
        ]
        source = {
            "스키마": "업무",
            "테이블명": "고객",
            "테이블설명": "고객정보",
            "컬럼명": "CUST_ID1",
            "컬럼설명": "고객ID번호1",
            "데이터타입": "VARCHAR",
        }
        result_headers = original_headers + list(ai_integrity.RESULT_COLUMNS)
        output = {
            **source,
            "한글속성명": "고객ID번호1",
            "처리상태": "자동확정",
            "신뢰도": 100,
            "처리방식": "유지",
            "변환근거": "",
            "검토사유": "",
        }
        terminology = [
            {
                "group_id": "customer",
                "candidates": ["고객", "손님"],
                "selected_term": "고객",
                "candidate_frequencies": {"고객": 1, "손님": 0},
                "tied": False,
                "affected_source_ids": ["row-2"],
            }
        ]
        report = ai_integrity.run_checks(
            ai_population.Table(original_headers, [source]),
            ai_population.Table(result_headers, [output]),
            terminology,
        )
        self.assertTrue(report["passed"])

        broken = dict(output, 한글속성명="고객FY번호2")
        report = ai_integrity.run_checks(
            ai_population.Table(original_headers, [source]),
            ai_population.Table(result_headers, [broken]),
            terminology,
        )
        self.assertFalse(report["passed"])
        self.assertFalse(report["checks"]["character_policy_complete"]["passed"])
        self.assertFalse(report["checks"]["numeric_preservation_complete"]["passed"])

        forged = [
            {
                **terminology[0],
                "candidate_frequencies": {"고객": 999, "손님": 0},
            }
        ]
        report = ai_integrity.run_checks(
            ai_population.Table(original_headers, [source]),
            ai_population.Table(result_headers, [output]),
            forged,
        )
        self.assertFalse(report["passed"])
        self.assertFalse(report["checks"]["terminology_frequency_verified"]["passed"])

        forged_tie = [
            {
                **terminology[0],
                "selected_term": "손님",
                "tied": True,
            }
        ]
        report = ai_integrity.run_checks(
            ai_population.Table(original_headers, [source]),
            ai_population.Table(result_headers, [output]),
            forged_tie,
        )
        self.assertFalse(report["passed"])
        self.assertFalse(report["checks"]["terminology_frequency_verified"]["passed"])

    def test_integrity_rejects_a_whole_missing_active_terminology_group(self) -> None:
        original_headers = ["컬럼명", "컬럼설명"]
        sources = [
            {"컬럼명": "PYM_CT", "컬럼설명": "납부횟수"},
            {"컬럼명": "USDCR_RT", "컬럼설명": "중고차율"},
        ]
        result_headers = original_headers + list(ai_integrity.RESULT_COLUMNS)
        outputs = [
            {
                **source,
                "한글속성명": source["컬럼설명"],
                "처리상태": "자동확정",
                "신뢰도": 100,
                "처리방식": "유지",
                "변환근거": "",
                "검토사유": "",
            }
            for source in sources
        ]
        payment_only = [
            {
                "group_id": "payment-action",
                "candidates": ["납입", "납부"],
                "selected_term": "납부",
                "candidate_frequencies": {"납입": 0, "납부": 1},
                "tied": False,
                "affected_source_ids": ["row-2"],
            }
        ]

        report = ai_integrity.run_checks(
            ai_population.Table(original_headers, sources),
            ai_population.Table(result_headers, outputs),
            payment_only,
        )

        self.assertFalse(report["passed"])
        issues = report["issues"]
        self.assertTrue(
            any(
                issue["check"] == "terminology_frequency_verified"
                and "active required terminology groups are missing" in issue["message"]
                and "used-car-rate" in issue["expected"]
                for issue in issues
            )
        )

    def test_population_is_unique_and_multi_stratified(self) -> None:
        original = ai_population.Table(
            ["테이블명", "테이블설명", "컬럼명", "컬럼설명", "데이터타입"],
            [
                {"테이블명": "T", "테이블설명": "회계", "컬럼명": "FY1", "컬럼설명": "FY년도1/기준년도1", "데이터타입": "N"},
                {"테이블명": "T", "테이블설명": "회계", "컬럼명": "FY1", "컬럼설명": "FY년도1/기준년도1", "데이터타입": "N"},
            ],
        )
        result_headers = original.headers + list(ai_integrity.RESULT_COLUMNS)
        outputs = [
            {
                **source,
                "한글속성명": "회계년도1",
                "처리상태": "검토필요",
                "신뢰도": 80,
                "처리방식": "재작성",
                "변환근거": "문맥 선택",
                "검토사유": "대안 확인",
            }
            for source in original.rows
        ]
        population = ai_population.build_population(
            original, ai_population.Table(result_headers, outputs), []
        )
        self.assertEqual(len(population), 1)
        self.assertEqual(population[0]["occurrence_count"], 2)
        self.assertTrue(
            {"english_translation", "numeric_preservation", "slash_context", "duplicate_context", "llm_rewrite", "review_needed"}
            <= set(population[0]["review_strata"])
        )


class HumanReviewSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.population = review_population()
        self.sample, self.manifest = human_select.select_new(
            self.population, "S4", HUMAN_RUBRIC
        )

    def test_rubric_weights_and_stage_seed_are_deterministic(self) -> None:
        self.assertEqual(
            sum(item["weight"] for item in HUMAN_RUBRIC["dimensions"]), 100
        )
        reordered = list(reversed(self.population))
        sample_again, manifest_again = human_select.select_new(
            reordered, "S4", HUMAN_RUBRIC
        )
        self.assertEqual(
            [row["source_id"] for row in self.sample],
            [row["source_id"] for row in sample_again],
        )
        self.assertEqual(self.manifest["selection_hash"], manifest_again["selection_hash"])
        _, other_manifest = human_select.select_new(
            self.population, "S5", HUMAN_RUBRIC
        )
        self.assertNotEqual(self.manifest["selection_hash"], other_manifest["selection_hash"])

    def test_locked_manifest_keeps_sources_and_csv_is_blind(self) -> None:
        changed = [dict(row, 한글속성명="변경속성") for row in self.population]
        locked, locked_manifest = human_select.select_locked(
            changed, "S4", self.manifest
        )
        self.assertEqual(
            [row["source_id"] for row in locked], self.manifest["source_ids"]
        )
        self.assertEqual(locked_manifest, self.manifest)
        path = PROJECT / "tests" / "_blind_sample_test.csv"
        self.addCleanup(path.unlink, missing_ok=True)
        human_select.write_csv(path, locked)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            headers = next(csv.reader(handle))
        for hidden in (
            "review_strata",
            "처리상태",
            "신뢰도",
            "변환근거",
            "ai_score",
        ):
            self.assertNotIn(hidden, headers)

    def test_converter_requires_real_human_completion_and_attestation(self) -> None:
        path = PROJECT / "tests" / "_ratings_sample_test.csv"
        self.addCleanup(path.unlink, missing_ok=True)
        human_select.write_csv(path, self.sample)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fieldnames = list(rows[0])
        for row in rows:
            for item in HUMAN_RUBRIC["dimensions"]:
                row[item["id"]] = "5"
            row["severity"] = "pass"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaises(ValueError):
            human_convert.convert(
                path,
                self.manifest,
                HUMAN_RUBRIC,
                "S4",
                "abc123",
                "stakeholder-1",
                False,
            )
        converted = human_convert.convert(
            path,
            self.manifest,
            HUMAN_RUBRIC,
            "S4",
            "abc123",
            "stakeholder-1",
            True,
        )
        self.assertEqual(len(converted["rows"]), len(self.sample))
        self.assertFalse(converted["review_provenance"]["ai_generated"])

    def test_perfect_review_passes_and_caps_are_enforced(self) -> None:
        review = perfect_human_review(self.manifest)
        perfect = human_score.score(HUMAN_RUBRIC, review)
        self.assertEqual(perfect["score"], 100)
        self.assertTrue(perfect["passed"])

        major = deepcopy(review)
        for row in major["rows"][:2]:
            row["severity"] = "major"
            row["comment"] = "업무 의미 수정 필요"
        major_result = human_score.score(HUMAN_RUBRIC, major)
        self.assertEqual(major_result["score"], 87)
        self.assertFalse(major_result["passed"])

        critical = deepcopy(review)
        critical["rows"][0]["severity"] = "critical"
        critical["rows"][0]["comment"] = "핵심 의미 반전"
        critical_result = human_score.score(HUMAN_RUBRIC, critical)
        self.assertEqual(critical_result["score"], 69)
        self.assertFalse(critical_result["passed"])

    def test_incomplete_sample_is_capped(self) -> None:
        review = perfect_human_review(self.manifest)
        review["rows"].pop()
        result = human_score.score(HUMAN_RUBRIC, review)
        self.assertEqual(result["score"], 59)
        self.assertFalse(result["passed"])

        review = perfect_human_review(self.manifest)
        review["sample_manifest"]["missing_population_strata"] = [
            "terminology_frequency"
        ]
        result = human_score.score(HUMAN_RUBRIC, review)
        self.assertEqual(result["score"], 59)
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
