def normalize_event(payload: dict, *, drop_empty: bool = False) -> dict:
    normalized = {}
    for key, value in payload.items():
        if drop_empty and value in (None, ""):
            continue
        normalized[key] = value
    normalized["schema_version"] = "1.8.2"
    return normalized
