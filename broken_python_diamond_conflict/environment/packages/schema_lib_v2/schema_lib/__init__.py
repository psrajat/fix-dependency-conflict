def normalize_event(payload: dict, *, required_fields=()) -> dict:
    missing = [field for field in required_fields if not payload.get(field)]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(sorted(missing))}")

    normalized = dict(payload)
    normalized["schema_version"] = "2.1.0"
    return normalized
