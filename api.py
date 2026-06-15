import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from services.runner import dump_forecast, run_forecast

api_bp = Blueprint("api", __name__)
logger = logging.getLogger(__name__)


@api_bp.route("/api/forecast", methods=["POST"])
def api_forecast():
    if not request.is_json:
        return jsonify({"error": "Ожидается application/json"}), 400
    try:
        payload: Dict[str, Any] = request.get_json(force=False, silent=False)  # type: ignore
    except Exception as e:
        return jsonify({"error": f"Некорректный JSON: {e}"}), 400

    try:
        result = run_forecast(payload)
        return jsonify(dump_forecast(result)), 200
    except ValueError as ve:
        # Business validation errors
        return jsonify({"error": str(ve)}), 400
    except ValidationError as e:
        return jsonify({"error": f"Ошибка валидации: {e}"}), 400
    except Exception as e:
        logger.exception("Forecast computation failed")
        return jsonify({"error": f"Внутренняя ошибка: {e}"}), 500



