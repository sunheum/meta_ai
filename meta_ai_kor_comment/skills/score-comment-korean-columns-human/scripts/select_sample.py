from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUBRIC = SKILL_DIR / "references" / "rubric.json"
VISIBLE_FIELDS = (
    ("source_id", ("source_id",)),
    ("테이블명", ("테이블명", "table_name")),
    ("테이블설명", ("테이블설명", "table_description")),
    ("컬럼명", ("컬럼명", "column_name")),
    ("컬럼설명", ("컬럼설명", "column_description")),
    ("한글속성명", ("한글속성명", "korean_attribute_name")),
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        value = json.loads(text)
        if isinstance(value, dict):
            value = value.get("rows", value.get("results"))
        rows = value
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(rows, list) or not rows or not all(
        isinstance(row, dict) for row in rows
    ):
        raise ValueError("population must contain a non-empty array of objects")
    source_ids = [str(row.get("source_id", "")).strip() for row in rows]
    if any(not source_id for source_id in source_ids):
        raise ValueError("every population row needs source_id")
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("population source_id values must be unique")
    return rows


def row_strata(row: dict[str, Any]) -> list[str]:
    value = row.get("review_strata")
    if isinstance(value, list):
        strata = [str(item).strip() for item in value if str(item).strip()]
    else:
        single = str(row.get("review_stratum", "")).strip()
        strata = [single] if single else []
    return sorted(set(strata))


def rank(stage_id: str, namespace: str, source_id: str) -> str:
    material = f"{stage_id}\0{namespace}\0{source_id}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def population_hash(rows: list[dict[str, Any]]) -> str:
    stable = [
        {"source_id": str(row["source_id"]), "strata": row_strata(row)}
        for row in sorted(rows, key=lambda item: str(item["source_id"]))
    ]
    payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_new(
    rows: list[dict[str, Any]], stage_id: str, rubric: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sample_rules = rubric["sample"]
    quotas = {key: int(value) for key, value in sample_rules["quotas"].items()}
    selected_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    available_counts = Counter(
        stratum for row in rows for stratum in row_strata(row)
    )
    for stratum in sample_rules["quota_order"]:
        candidates = [
            row
            for row in rows
            if stratum in row_strata(row) and str(row["source_id"]) not in selected_ids
        ]
        candidates.sort(
            key=lambda row: (
                rank(stage_id, stratum, str(row["source_id"])),
                str(row["source_id"]),
            )
        )
        for row in candidates[: quotas[stratum]]:
            selected.append(row)
            selected_ids.add(str(row["source_id"]))
    remaining = [row for row in rows if str(row["source_id"]) not in selected_ids]
    remaining.sort(
        key=lambda row: (
            rank(stage_id, "minimum-fill", str(row["source_id"])),
            str(row["source_id"]),
        )
    )
    needed = max(0, int(sample_rules["minimum_size"]) - len(selected))
    selected.extend(remaining[:needed])

    source_ids = [str(row["source_id"]) for row in selected]
    source_strata = {str(row["source_id"]): row_strata(row) for row in selected}
    selected_counts = Counter(
        stratum for strata in source_strata.values() for stratum in strata
    )
    required_counts = {
        stratum: min(quotas[stratum], available_counts[stratum])
        for stratum in sample_rules["required_strata"]
    }
    missing_population_strata = [
        stratum
        for stratum in sample_rules["required_strata"]
        if available_counts[stratum] == 0
    ]
    manifest = {
        "schema_version": "1.0.0",
        "stage_id": stage_id,
        "seed_sha256": hashlib.sha256(stage_id.encode("utf-8")).hexdigest(),
        "population_size": len(rows),
        "population_hash": population_hash(rows),
        "minimum_size": int(sample_rules["minimum_size"]),
        "quotas": quotas,
        "available_counts": dict(available_counts),
        "required_counts": required_counts,
        "missing_population_strata": missing_population_strata,
        "selected_counts": dict(selected_counts),
        "source_ids": source_ids,
        "source_strata": source_strata,
        "selection_hash": hashlib.sha256("\n".join(source_ids).encode("utf-8")).hexdigest(),
    }
    return selected, manifest


def select_locked(
    rows: list[dict[str, Any]], stage_id: str, manifest: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if manifest.get("stage_id") != stage_id:
        raise ValueError("selection manifest stage_id does not match")
    source_ids = manifest.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids:
        raise ValueError("selection manifest has no source_ids")
    by_id = {str(row["source_id"]): row for row in rows}
    missing = [str(source_id) for source_id in source_ids if str(source_id) not in by_id]
    if missing:
        raise ValueError(f"locked sample sources are missing: {', '.join(missing)}")
    selected = [by_id[str(source_id)] for source_id in source_ids]
    expected_hash = hashlib.sha256(
        "\n".join(str(source_id) for source_id in source_ids).encode("utf-8")
    ).hexdigest()
    if manifest.get("selection_hash") != expected_hash:
        raise ValueError("selection manifest hash is invalid")
    return selected, manifest


def visible_value(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row:
            return row.get(alias)
    return None


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    rating_fields = [
        "business_semantic_accuracy",
        "meaning_completeness",
        "character_notation_policy",
        "context_ambiguity_safety",
        "terminology_consistency",
        "naturalness_conciseness",
        "severity",
        "comment",
    ]
    fieldnames = [name for name, _ in VISIBLE_FIELDS] + rating_fields
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            visible = {
                name: visible_value(row, aliases) for name, aliases in VISIBLE_FIELDS
            }
            visible.update({field: "" for field in rating_fields})
            writer.writerow(visible)


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--selection-manifest", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    args = parser.parse_args()
    rows = load_rows(args.input)
    rubric = read_json(args.rubric)
    if args.selection_manifest:
        selected, manifest = select_locked(
            rows, args.stage_id, read_json(args.selection_manifest)
        )
    else:
        selected, manifest = select_new(rows, args.stage_id, rubric)
    manifest_path = args.manifest_output or args.output.with_suffix(".manifest.json")
    write_csv(args.output, selected)
    write_manifest(manifest_path, manifest)
    print(
        f"human review sample rows={len(selected)} stage_id={args.stage_id} "
        f"manifest={manifest_path}"
    )


if __name__ == "__main__":
    main()
