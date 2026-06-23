from typing import Any, Dict

from services.forecast import compute_forecast
from services.schemas import ForecastOutput, InputSchema


def validate_input(payload: Dict[str, Any]) -> InputSchema:
    try:
        return InputSchema.model_validate(payload)
    except AttributeError:
        return InputSchema.parse_obj(payload)  # type: ignore


def run_forecast(payload: Dict[str, Any]) -> ForecastOutput:
    return compute_forecast(validate_input(payload))


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, float):
        if value == float("inf") or value == float("-inf"):
            return None
        return value
    if isinstance(value, dict):
        return {key: _sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    return value


def dump_forecast(result: ForecastOutput) -> Dict[str, Any]:
    try:
        payload = result.model_dump()
    except AttributeError:
        payload = result.dict()
    return _sanitize_json_value(payload)
