## Сервис прогнозирования нагрузки и задержек (Flask + Jinja2 + ECharts + Pydantic)

Полноценный веб‑сервис, который принимает результаты ступенчатого нагрузочного теста (RPS, avg_ms, max_ms, утилизации) и строит человекочитаемые прогнозы по задержкам (средним и максимальным) и утилизации ресурсов, а также рекомендации по масштабированию.  
Интерактивные графики отрисовываются в UI с помощью Apache ECharts; серверные шаблоны на Jinja2; валидация входа — Pydantic; математика и подгонка моделей — numpy/pandas/scipy.

### Ключевые возможности
- Модели: M/M/1, M/M/c (Erlang C), Kingman (G/G/1), G/G/c (Allen–Cunneen).
- Оценка сервисного времени S и устройств D_i (CPU/RAM/IO) по линейному участку (утилизация vs RPS).
- Прогноз задержек при целевом `target_rps`, проверка SLO по максимальной задержке.
- Рекомендации по масштабированию: инстансы по CPU/RAM и подбор `c` для M/M/c и G/G/c.
- Последовательный ввод ступеней через форму и сохранение наборов данных в `data/`.
- Интерактивные графики: latency vs RPS (+линия SLO), CPU/RAM utilization vs RPS, Instances vs RPS.
- Экспорт PDF отчёта (опционально через WeasyPrint).
- REST API (`POST /api/forecast`) и OpenAPI (`GET /openapi.json`).

---

### Быстрый старт (локально)
1) Требования: Python 3.10+ (рекомендуется 3.11), macOS/Linux/WSL.  
2) Установка:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```
3) Запуск в dev‑режиме:
```bash
export DEBUG=1
python app.py
# или через gunicorn (как в проде):
# gunicorn -w 4 -b 0.0.0.0:8000 app:app
```
4) Откройте браузер: `http://127.0.0.1:8000`

5) На главной странице заполните параметры прогноза и ступени нагрузочного теста либо загрузите сохранённый набор данных, затем нажмите «Рассчитать прогноз».

Примечание (PDF на macOS): для WeasyPrint могут понадобиться системные библиотеки (Pango, Cairo). На macOS можно установить через `brew install pango cairo`. Если не требуется PDF, WeasyPrint можно пропустить.

---

### Docker
```bash
docker build -t forecast-svc .
docker run --rm -p 8000:8000 -e PORT=8000 forecast-svc
```
Затем откройте `http://127.0.0.1:8000`.

---

### Стек и зависимости
- Flask (роутинг, серверные шаблоны), Jinja2 (рендеринг HTML)
- pandas/numpy/scipy (обработка данных, регрессии, математика очередей)
- Apache ECharts (интерактивные графики), Bootstrap 5 (вёрстка)
- Pydantic (валидация входного/выходного JSON)
- WeasyPrint (опционально, экспорт PDF)
- Gunicorn (продовый запуск)

Все необходимые пакеты перечислены в `requirements.txt`.

---

### Структура проекта
- `app.py` — создание Flask‑приложения; HTML‑роуты (`/`, `/forecast`, `/export/pdf`), OpenAPI (`/openapi.json`), `healthz`.
- `api.py` — REST API `POST /api/forecast` (валидация Pydantic, детальные 400‑ошибки).
- `services/schemas.py` — Pydantic‑схемы входа/выхода (InputSchema, ForecastOutput и т.д.).
- `services/models.py` — функции моделей (M/M/1, M/M/c, Kingman (G/G/1), G/G/c).
- `services/forecast.py` — логика валидации, регрессий и прогноза (Kingman, G/G/c)
- `templates/index.html` — главная страница с пошаговой формой, выбором/сохранением датасета и подсказками.
- `templates/results.html` — отчёт с таблицами и графиками.
- `static/js/app.js` — динамическая форма ступеней, сохранение датасетов и графики ECharts.
- `static/css/style.css` — пользовательские стили.
- `data/sample.json` — пример входного JSON.
- `tests/test_models.py` — базовые тесты моделей/прогноза.
- `Dockerfile` — multi‑stage образ для прод‑запуска (gunicorn).

---

### Описание графиков и таблиц в отчёте

1) Таблица «Модели и прогнозы (мс)»  
- «Средняя задержка (мс)»: прогноз среднего времени отклика при целевом RPS по каждой модели.  
- «Максимальная (мс)»: пиковая задержка, оценивается как средняя × коэффициент (отношение `max_ms/avg_ms` базовой ступени).  
- «Рекомендация»: краткий совет по применимости модели (риск SLO, увеличение `c` и т.п.).  
- Модели: M/M/1, M/M/c (Erlang C), Kingman (G/G/1), G/G/c (Allen–Cunneen).

2) Таблица «Параметры и оценки»  
- `S (мс)`: базовое сервисное время. Оценивается по максимальному наклону утилизаций на линейном участке: `S ≈ max(D_i)`.  
- `D_cpu`, `D_ram` (мс/запрос): сервисные требования ресурсов, получаются регрессией `util ~ rps`.  
- `Ca`, `Cs`: коэффициенты вариации входа и сервиса (для Кингмана и Allen–Cunneen).  
- Requests/Limits per pod (если доступны): агрегированные средние значения используются в ресурсной таблице («Kubernetes профиль»).

3) График «Задержка vs RPS»  
- Точки Observed — фактические средние и максимальные задержки на ступенях теста.  
- Линии M/M/1, M/M/c, G/G/1, G/G/c — прогнозы средних задержек по моделям.  
- Вертикальная пунктирная — целевой RPS.  
- Горизонтальная линия SLO — порог по максимальной задержке (если задан в `target.slo_ms_max_optional`).

4) График «CPU/RAM утилизация vs RPS»  
- Линии CPU и RAM — прогноз утилизаций `U_i = X · D_i`.  
- Горизонтальные пороги `u_max_cpu`/`u_max_ram` показывают границы, где потребуется масштабирование.

5) График «Инстансы vs RPS»  
- Ступенчатые линии показывают необходимое количество инстансов по CPU и RAM.  
- Изломы соответствуют событиям масштабирования (увеличение числа инстансов).

---

### Формат входного JSON (кратко)
Kubernetes-поля (см. `data/sample.json`):

В каждой ступени `rps` — общий RPS по всем pod’ам, `pods` — число pod’ов на этой ступени. Поля `cpu_usage_m` и `mem_workingset_mib` вводятся как средние значения на один pod; requests/limits также задаются на один pod.

```json
{
  "steps": [
    {
      "step": "s1",
      "rps": 62,
      "avg_ms": 540,
      "max_ms": 586,
      "pods": 2,
      "cpu_usage_m": 520,
      "cpu_request_m_per_pod": 260,
      "cpu_limit_m_per_pod": 1200,
      "mem_workingset_mib": 2380,
      "mem_request_mib_per_pod": 1290,
      "mem_limit_mib_per_pod": 1400,
      "concurrency_optional": 33,
      "errors_pct": 0.0
    }
    // ... минимум 2 ступени
  ],
  "target": {
    "target_rps": 150,
    "slo_ms_max_optional": 200
  },
  "capacity": {
    "u_max_cpu": 0.7,
    "u_max_ram": 0.7,
    "mmc_c_optional": 4
  },
  "modeling": {
    "use_m_m_1": true,
    "use_m_m_c": true,
    "use_kingman": true,
    "use_g_g_c": true
  }
}
```
Ограничения: `