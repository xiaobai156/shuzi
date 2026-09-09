from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(
            f"{path}: expected one match, got {text.count(old)}: {old[:120]}"
        )
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


# A dedicated parser has already established its exact article/section
# contract. Requiring a generic keyword on every history row would reject
# valid rows whose keyword appears only in the article heading.
replace_once(
    "kill_numbers/validation/result_validator.py",
    '''        allowed_starts = issue_position_window_starts(
            section,
            target_keywords(dict(target)),
            target.get("count"),
''',
    '''        evidence_keywords = (
            target_keywords(dict(target))
            if parser_id == "generic"
            else []
        )
        allowed_starts = issue_position_window_starts(
            section,
            evidence_keywords,
            target.get("count"),
''',
)

# Cache output order follows the retained timeline. This preserves existing
# order during a historical correction and avoids ordering sites by URL.
replace_once(
    "kill_numbers/infrastructure/cache_repository.py",
    '''    combined_records = [
        record
        for key, record in record_payload.items()
        if key in retained_payload_keys
    ]
    combined_failures = [
        record
        for key, record in failure_payload.items()
        if key in retained_payload_keys
    ]
    combined_records.sort(key=lambda record: (record["url"].lower(), record["name"], int(record["issue"])))
    combined_failures.sort(key=lambda record: (record["url"].lower(), record["name"], int(record["issue"])))
''',
    '''    combined_records: list[dict] = []
    combined_failures: list[dict] = []
    for entry in retained_timeline:
        key = (_site_key(entry["name"], entry["url"]), entry["issue"])
        if entry["status"] == "success":
            record = record_payload.get(key)
            if record is not None:
                combined_records.append(record)
        else:
            failure = failure_payload.get(key)
            if failure is not None:
                combined_failures.append(failure)
''',
)

print("final critical corrections applied")
