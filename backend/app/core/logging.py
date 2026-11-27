"""Small structured logging setup with secret-safe defaults."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            payload["request_id"] = record.request_id
        # Keep the structured diagnostics explicitly allowlisted. Never serialize
        # arbitrary LogRecord extras, exception objects, headers, or request data.
        for field in ("provider", "category", "stage", "upstream_status_code", "rpc_status", "reason"):
            value = getattr(record, field, None)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                payload[field] = value
        for diagnostic_field in ("validation_errors", "structural_errors"):
            diagnostics = getattr(record, diagnostic_field, None)
            if not isinstance(diagnostics, (list, tuple)):
                continue
            safe_errors = []
            allowed_scalar_fields = (
                "type", "validation_category", "field_path", "expected", "expected_type",
                "actual_type", "missing_required", "json_root_type",
            )
            allowed_list_fields = ("top_level_keys", "missing_keys", "unexpected_keys")
            for item in diagnostics[:20]:
                if not isinstance(item, dict):
                    continue
                safe_item = {
                    key: item[key]
                    for key in allowed_scalar_fields
                    if isinstance(item.get(key), (str, bool))
                }
                for key in allowed_list_fields:
                    values = item.get(key)
                    if isinstance(values, (list, tuple)):
                        safe_item[key] = [value for value in values[:20] if isinstance(value, str)]
                safe_errors.append(safe_item)
            if safe_errors:
                payload[diagnostic_field] = safe_errors
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Install a JSON formatter without logging request or credential data."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
