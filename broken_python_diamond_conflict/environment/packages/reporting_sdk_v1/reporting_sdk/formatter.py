from schema_lib import normalize_event


def build_report_record(payload: dict) -> dict:
    normalized = normalize_event(payload, drop_empty=True)
    return {
        "group": normalized.get("source", "unknown"),
        "user_id": normalized.get("user_id"),
        "schema_version": normalized["schema_version"],
    }
