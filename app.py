import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

from flask import (
    Flask,
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    session,
    flash,
    make_response,
)

from api import api_bp
from services.schemas import InputSchema, ForecastOutput
from services.forecast import compute_forecast


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    # Security and limits
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1MB JSON limit
    app.config["JSON_SORT_KEYS"] = False

    # Logging
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("DEBUG") == "1" else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    @app.after_request
    def set_security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-XSS-Protection"] = "1; mode=block"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return jsonify({"status": "ok"}), 200

    @app.route("/", methods=["GET"])
    def index():
        # Simple CSRF token for form posts
        token = os.urandom(16).hex()
        session["csrf_token"] = token
        # Populate sample on UI for convenience
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        sample_path = os.path.join(data_dir, "sample.json")
        sample_json_str = ""
        try:
            with open(sample_path, "r", encoding="utf-8") as f:
                sample_json_str = f.read()
        except Exception:
            sample_json_str = ""
        # Make dataset names list from data directory (*.json)
        dataset_names = []
        try:
            for fn in sorted(os.listdir(data_dir)):
                if fn.lower().endswith(".json"):
                    dataset_names.append(os.path.splitext(fn)[0])
        except Exception:
            dataset_names = []
        return render_template(
            "index.html",
            csrf_token=token,
            sample_json=sample_json_str,
            dataset_names=dataset_names,
        )

    @app.route("/forecast", methods=["POST"])
    def forecast_html():
        # CSRF check for HTML form
        form_token = request.form.get("csrf_token", "")
        if not form_token or form_token != session.get("csrf_token"):
            flash("Неверный CSRF токен. Обновите страницу.", "danger")
            return redirect(url_for("index"))

        json_text = request.form.get("json_input", "").strip()
        target_rps = request.form.get("target_rps", "").strip()
        slo_ms = request.form.get("slo_ms_max_optional", "").strip()

        if not json_text:
            flash("Пустой JSON.", "danger")
            return redirect(url_for("index"))

        try:
            payload = json.loads(json_text)
        except Exception as e:
            flash(f"Некорректный JSON: {e}", "danger")
            return redirect(url_for("index"))

        # Override target fields from form if provided
        try:
            if "target" not in payload:
                payload["target"] = {}
            if target_rps:
                payload["target"]["target_rps"] = float(target_rps)
            if slo_ms:
                payload["target"]["slo_ms_max_optional"] = float(slo_ms)
            # think_time removed
        except Exception:
            flash("Числовые поля формы содержат ошибки.", "danger")
            return redirect(url_for("index"))

        try:
            validated = InputSchema.model_validate(payload)  # Pydantic v2
        except AttributeError:
            # Pydantic v1 fallback
            validated = InputSchema.parse_obj(payload)  # type: ignore
        except Exception as e:
            flash(f"Ошибка валидации: {e}", "danger")
            return redirect(url_for("index"))

        try:
            result = compute_forecast(validated)
        except Exception as e:
            logging.exception("Forecast computation failed")
            flash(f"Ошибка расчёта прогноза: {e}", "danger")
            return redirect(url_for("index"))

        # Keep JSON and rendered report for optional PDF export
        try:
            result_json = result.model_dump()  # v2
        except AttributeError:
            result_json = result.dict()  # v1
        session["last_forecast_json"] = json.dumps(result_json, ensure_ascii=False)

        # Render results page
        rendered = render_template(
            "results.html",
            now=datetime.utcnow().isoformat() + "Z",
            target_rps=result_json["targets"]["rps"],
            result_json=result_json,
        )
        # Store rendered for PDF export
        session["last_report_html"] = rendered
        return rendered

    @app.route("/export/pdf", methods=["GET", "POST"])
    def export_pdf():
        # Prefer payload from POST body to avoid session size limits
        incoming = None
        if request.method == "POST":
            incoming = request.form.get("payload")
        # Re-render results from the last forecast JSON stored in session to avoid huge cookies
        last_json_str = incoming or session.get("last_forecast_json")
        if not last_json_str:
            flash("Нет отчёта для экспорта. Сначала выполните расчёт.", "warning")
            return redirect(url_for("index"))
        try:
            result_json = json.loads(last_json_str) if isinstance(last_json_str, str) else last_json_str
            target_rps = result_json.get("targets", {}).get("rps", 0)
            html = render_template(
                "results.html",
                now=datetime.utcnow().isoformat() + "Z",
                target_rps=target_rps,
                result_json=result_json,
            )
        except Exception as e:
            flash(f"Не удалось восстановить отчёт из последнего прогноза: {e}", "warning")
            return redirect(url_for("index"))
        try:
            # Optional dependency
            from weasyprint import HTML  # type: ignore

            pdf_bytes = HTML(string=html, base_url=request.url_root).write_pdf()
            resp = make_response(pdf_bytes)
            resp.headers["Content-Type"] = "application/pdf"
            resp.headers[
                "Content-Disposition"
            ] = 'attachment; filename="forecast_report.pdf"'
            return resp
        except Exception as e:
            flash(f"Экспорт PDF недоступен: {e}", "warning")
            return redirect(url_for("index"))

    @app.route("/openapi.json", methods=["GET"])
    def openapi_json():
        # Build simple OpenAPI 3.0 spec with our schemas
        try:
            input_schema = InputSchema.model_json_schema()  # v2
        except AttributeError:
            input_schema = InputSchema.schema()  # v1
        try:
            output_schema = ForecastOutput.model_json_schema()  # v2
        except AttributeError:
            output_schema = ForecastOutput.schema()  # v1

        spec: Dict[str, Any] = {
            "openapi": "3.0.3",
            "info": {
                "title": "Forecast Service API",
                "version": "1.0.0",
                "description": (
                    "API для приёма результатов ступенчатого нагрузочного теста и "
                    "построения прогнозов по утилизации и задержкам."
                ),
            },
            "paths": {
                "/api/forecast": {
                    "post": {
                        "summary": "Рассчитать прогноз",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": input_schema,
                                    "examples": {
                                        "sample": {
                                            "summary": "Пример входного JSON",
                                            "value": json.loads(
                                                session.get("last_forecast_json", "null")
                                            )
                                            or _read_sample_json()
                                        }
                                    },
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "Успешный ответ с прогнозом",
                                "content": {
                                    "application/json": {
                                        "schema": output_schema
                                    }
                                },
                            },
                            "400": {
                                "description": "Ошибка валидации или данных",
                            },
                        },
                    }
                },
                "/healthz": {
                    "get": {
                        "summary": "Проверка работоспособности",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
            "components": {"schemas": {"InputSchema": input_schema, "ForecastOutput": output_schema}},
        }
        return jsonify(spec)

    @app.route("/data/get/<string:name>", methods=["GET"])
    def get_dataset(name: str):
        # allow only files present in data dir and safe names
        safe = "".join(ch for ch in name if (ch.isalnum() or ch in ("-", "_")))
        if not safe:
            return jsonify({"error": "invalid dataset name"}), 400
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        path = os.path.join(data_dir, f"{safe}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = json.load(f)
            return jsonify(content), 200
        except FileNotFoundError:
            return jsonify({"error": "dataset not found"}), 404
        except Exception as e:
            return jsonify({"error": f"failed to load dataset: {e}"}), 500

    # removed AJAX-only preview route

    def _read_sample_json() -> Any:
        sample_path = os.path.join(os.path.dirname(__file__), "data", "sample.json")
        try:
            with open(sample_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG") == "1")


