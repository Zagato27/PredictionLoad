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


def dump_forecast(result: ForecastOutput) -> Dict[str, Any]:
    try:
        return result.model_dump()
    except AttributeError:
        return result.dict()
