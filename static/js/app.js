function finiteOrNull(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function roundOrNull(value, digits) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function hasNumericValues(values) {
  return values.some((value) => typeof value === 'number' && Number.isFinite(value));
}

function firstCrossing(points, valueSelector, threshold) {
  if (threshold === null || !Number.isFinite(threshold)) return null;
  for (const point of points) {
    const value = valueSelector(point);
    if (typeof value === 'number' && Number.isFinite(value) && value >= threshold) {
      return point;
    }
  }
  return null;
}

function firstBelow(points, valueSelector, threshold) {
  if (threshold === null || !Number.isFinite(threshold)) return null;
  for (const point of points) {
    const value = valueSelector(point);
    if (typeof value === 'number' && Number.isFinite(value) && value <= threshold) {
      return point;
    }
  }
  return null;
}

function maxFinite(values, fallback) {
  const finiteValues = values.filter((value) => typeof value === 'number' && Number.isFinite(value));
  return finiteValues.length ? Math.max(...finiteValues) : fallback;
}

function formatNumber(value, digits) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'n/a';
  return value.toFixed(digits);
}

function formatSignedNumber(value, digits) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'n/a';
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
}

function downloadTextFile(filename, text) {
  const blob = new Blob([text], {type: 'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function buildCompactForecastReport(forecast, pageMeta) {
  const meta = forecast.meta || {};
  const targets = forecast.targets || {};
  const instances = targets.instances || {};
  const utilization = targets.utilization || {};
  const models = forecast.models || {};
  const kube = models.kube || {};
  const latency = targets.latency_ms || {};
  const primaryModelKey = meta.primary_model === 'G/G/c' ? 'g_g_c' : 'm_m_c';
  const primaryLatency = latency[primaryModelKey] || latency.g_g_c || latency.m_m_c || null;
  const sloStatus = meta.slo_status === 'ok' ? 'OK' : (meta.slo_status === 'risk' ? 'РИСК' : 'не задан');
  const lines = [
    'Краткий прогноз нагрузки',
    `Дата расчёта: ${(pageMeta && pageMeta.now) || 'n/a'}`,
    '',
    'Вердикт',
    `- Target RPS: ${formatNumber(targets.rps, 1)}`,
    `- SLO: ${meta.slo_ms_max_optional ? `${meta.slo_ms_max_optional} мс` : 'не задан'}`,
    `- Статус SLO: ${sloStatus}`,
    `- Запас SLO: ${formatSignedNumber(meta.slo_margin_ms, 1)} мс`,
    `- Основная модель: ${meta.primary_model || 'n/a'}`,
    `- Прогноз max latency: ${formatNumber(primaryLatency && primaryLatency.max, 1)} мс`,
    `- Прогноз avg latency: ${formatNumber(primaryLatency && primaryLatency.avg, 1)} мс`,
    '',
    'Рекомендация',
    `- Реплики: ${instances.suggested_m || meta.recommended_replicas || 'n/a'}`,
    `- Ограничивающий ресурс: ${meta.bottleneck || 'n/a'}`,
    `- CPU-based: ${instances.cpu_based || 'n/a'}`,
    `- RAM-based: ${instances.ram_based || 'n/a'}`,
    '',
    'Ресурсы на target RPS',
    `- CPU util: ${formatNumber((utilization.cpu || 0) * 100, 1)}% при пороге ${meta.u_max_cpu ? formatNumber(Number(meta.u_max_cpu) * 100, 1) : 'n/a'}%`,
    `- RAM util: ${formatNumber((utilization.ram || 0) * 100, 1)}% при пороге ${meta.u_max_ram ? formatNumber(Number(meta.u_max_ram) * 100, 1) : 'n/a'}%`,
    `- CPU запас: ${formatSignedNumber(meta.cpu_headroom_pct, 1)}%`,
    `- RAM запас: ${formatSignedNumber(meta.ram_headroom_pct, 1)}%`,
    '',
    'Kubernetes requests/limits',
    `- CPU request/limit на pod: ${formatNumber(kube.cpu_request_m_per_pod, 0)}m / ${formatNumber(kube.cpu_limit_m_per_pod, 0)}m`,
    `- RAM request/limit на pod: ${formatNumber(kube.mem_request_mib_per_pod, 0)} MiB / ${formatNumber(kube.mem_limit_mib_per_pod, 0)} MiB`,
    '',
    'Качество прогноза',
    `- Использовано ступеней: ${meta.used_steps || 'n/a'} из ${meta.observed_steps || 'n/a'}`,
    `- Исключено ступеней: ${meta.excluded_steps || 0}`,
    `- Линейный участок: ${meta.linear_steps || 'n/a'}`,
    `- Target / max observed RPS: ${formatNumber(meta.target_over_observed_ratio, 2)}x`,
    `- Service time S: ${formatNumber(models.service_time_ms, 1)} мс`,
    '',
    'Предупреждения',
    ...(Array.isArray(meta.quality_warnings) ? meta.quality_warnings.map((warning) => `- ${warning}`) : ['- n/a']),
  ];
  return `${lines.join('\n')}\n`;
}

window.downloadTextFile = downloadTextFile;
window.buildCompactForecastReport = buildCompactForecastReport;

function numberValue(root, selector, required) {
  const element = root.querySelector(selector);
  if (!element || element.value === '') {
    if (required) {
      throw new Error('Заполните обязательные числовые поля.');
    }
    return null;
  }
  const value = Number(element.value);
  if (!Number.isFinite(value)) {
    throw new Error('Некорректное числовое значение.');
  }
  return value;
}

function setStatus(message, isError) {
  const status = document.getElementById('form-status');
  if (!status) return;
  status.textContent = message || '';
  status.classList.toggle('text-danger', Boolean(isError));
  status.classList.toggle('text-success', Boolean(message && !isError));
}

function stepCards() {
  return Array.from(document.querySelectorAll('[data-step-card]'));
}

function refreshStepNumbers() {
  const cards = stepCards();
  cards.forEach((card, index) => {
    const numberElement = card.querySelector('[data-step-number]');
    const removeButton = card.querySelector('[data-remove-step]');
    if (numberElement) numberElement.textContent = String(index + 1);
    if (removeButton) removeButton.disabled = cards.length <= 2;
  });
}

function setFieldValue(root, field, value) {
  const element = root.querySelector(`[data-field="${field}"]`);
  if (element) {
    element.value = value == null ? '' : String(value);
  }
}

function addStep(step) {
  const template = document.getElementById('step-template');
  const container = document.getElementById('steps-container');
  if (!template || !container) return null;
  const fragment = template.content.cloneNode(true);
  const card = fragment.querySelector('[data-step-card]');
  container.appendChild(fragment);
  const createdCard = container.lastElementChild;
  const values = step || {};
  setFieldValue(createdCard, 'step', values.step || `s${stepCards().length}`);
  setFieldValue(createdCard, 'rps', values.rps);
  setFieldValue(createdCard, 'avg_ms', values.avg_ms);
  setFieldValue(createdCard, 'max_ms', values.max_ms);
  setFieldValue(createdCard, 'errors_pct', values.errors_pct == null ? 0 : values.errors_pct);
  setFieldValue(createdCard, 'pods', values.pods == null ? 1 : values.pods);
  setFieldValue(createdCard, 'cpu_usage_m', values.cpu_usage_m);
  setFieldValue(createdCard, 'cpu_request_m_per_pod', values.cpu_request_m_per_pod);
  setFieldValue(createdCard, 'cpu_limit_m_per_pod', values.cpu_limit_m_per_pod);
  setFieldValue(createdCard, 'mem_workingset_mib', values.mem_workingset_mib);
  setFieldValue(createdCard, 'mem_request_mib_per_pod', values.mem_request_mib_per_pod);
  setFieldValue(createdCard, 'mem_limit_mib_per_pod', values.mem_limit_mib_per_pod);
  const removeButton = createdCard.querySelector('[data-remove-step]');
  if (removeButton) {
    removeButton.addEventListener('click', () => {
      if (stepCards().length <= 2) return;
      createdCard.remove();
      refreshStepNumbers();
    });
  }
  refreshStepNumbers();
  return createdCard;
}

function collectStep(card) {
  const stepInput = card.querySelector('[data-field="step"]');
  const step = stepInput && stepInput.value ? stepInput.value.trim() : '';
  if (!step) {
    throw new Error('У каждой ступени должен быть ID.');
  }
  const avgMs = numberValue(card, '[data-field="avg_ms"]', true);
  const maxMs = numberValue(card, '[data-field="max_ms"]', true);
  if (maxMs < avgMs) {
    throw new Error('Max latency не может быть меньше Avg latency.');
  }
  const result = {
    step,
    rps: numberValue(card, '[data-field="rps"]', true),
    avg_ms: avgMs,
    max_ms: maxMs,
    errors_pct: numberValue(card, '[data-field="errors_pct"]', true),
    pods: numberValue(card, '[data-field="pods"]', true),
    cpu_usage_m: numberValue(card, '[data-field="cpu_usage_m"]', true),
    cpu_request_m_per_pod: numberValue(card, '[data-field="cpu_request_m_per_pod"]', true),
    mem_workingset_mib: numberValue(card, '[data-field="mem_workingset_mib"]', true),
    mem_request_mib_per_pod: numberValue(card, '[data-field="mem_request_mib_per_pod"]', true),
  };
  const cpuLimit = numberValue(card, '[data-field="cpu_limit_m_per_pod"]', false);
  const memLimit = numberValue(card, '[data-field="mem_limit_mib_per_pod"]', false);
  if (cpuLimit !== null) result.cpu_limit_m_per_pod = cpuLimit;
  if (memLimit !== null) result.mem_limit_mib_per_pod = memLimit;
  return result;
}

function collectFormPayload() {
  const form = document.getElementById('forecast-form');
  const cards = stepCards();
  if (!form || cards.length < 2) {
    throw new Error('Добавьте минимум две ступени.');
  }
  const payload = {
    steps: cards.map(collectStep),
    target: {
      target_rps: numberValue(form, '#target_rps', true),
    },
    capacity: {
      u_max_cpu: numberValue(form, '#u_max_cpu', true),
      u_max_ram: numberValue(form, '#u_max_ram', true),
      mmc_c_optional: numberValue(form, '#mmc_c_optional', true),
    },
    modeling: {
      use_m_m_1: true,
      use_m_m_c: true,
      use_kingman: true,
      use_g_g_c: true,
    },
  };
  const slo = numberValue(form, '#slo_ms_max_optional', false);
  if (slo !== null) payload.target.slo_ms_max_optional = slo;
  return payload;
}

function fillFormFromPayload(payload) {
  const form = document.getElementById('forecast-form');
  const container = document.getElementById('steps-container');
  if (!form || !container || !payload) return;
  form.querySelector('#target_rps').value = payload.target && payload.target.target_rps != null ? payload.target.target_rps : '';
  form.querySelector('#slo_ms_max_optional').value = payload.target && payload.target.slo_ms_max_optional != null ? payload.target.slo_ms_max_optional : '';
  form.querySelector('#u_max_cpu').value = payload.capacity && payload.capacity.u_max_cpu != null ? payload.capacity.u_max_cpu : '';
  form.querySelector('#u_max_ram').value = payload.capacity && payload.capacity.u_max_ram != null ? payload.capacity.u_max_ram : '';
  form.querySelector('#mmc_c_optional').value = payload.capacity && payload.capacity.mmc_c_optional != null ? payload.capacity.mmc_c_optional : 1;
  container.innerHTML = '';
  const steps = Array.isArray(payload.steps) && payload.steps.length >= 2 ? payload.steps : [{}, {}];
  steps.forEach((step) => addStep(step));
  setStatus('', false);
}

async function saveDataset() {
  try {
    const payload = collectFormPayload();
    const nameInput = document.getElementById('dataset-name');
    const csrfInput = document.querySelector('input[name="csrf_token"]');
    const name = nameInput ? nameInput.value.trim() : '';
    if (!name) {
      throw new Error('Укажите название набора данных.');
    }
    const response = await fetch('/data/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name,
        payload,
        csrf_token: csrfInput ? csrfInput.value : '',
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result && result.error ? result.error : 'Не удалось сохранить набор.');
    }
    const select = document.getElementById('dataset-select');
    if (select && result.name) {
      let option = Array.from(select.options).find((item) => item.value === result.name);
      if (!option) {
        option = new Option(result.name, result.name);
        select.add(option);
      }
      select.value = result.name;
    }
    setStatus('Набор данных сохранён.', false);
  } catch (error) {
    setStatus(error && error.message ? error.message : 'Ошибка сохранения.', true);
  }
}

function initStepForm() {
  const form = document.getElementById('forecast-form');
  const initialPayload = document.getElementById('initial-payload');
  if (!form || !initialPayload) return;
  let payload = null;
  try {
    payload = JSON.parse(initialPayload.textContent || '{}');
  } catch (error) {
    payload = null;
  }
  fillFormFromPayload(payload);
  document.getElementById('btn-add-step-top')?.addEventListener('click', () => addStep());
  document.getElementById('btn-add-step-bottom')?.addEventListener('click', () => addStep());
  document.getElementById('btn-save-dataset')?.addEventListener('click', saveDataset);
  document.getElementById('dataset-select')?.addEventListener('change', async (event) => {
    const name = event.target.value;
    if (!name) return;
    try {
      const response = await fetch(`/data/get/${encodeURIComponent(name)}`);
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data && data.error ? data.error : 'Не удалось загрузить набор.');
      }
      const nameInput = document.getElementById('dataset-name');
      if (nameInput) nameInput.value = name;
      fillFormFromPayload(data);
    } catch (error) {
      setStatus(error && error.message ? error.message : 'Ошибка загрузки набора.', true);
    }
  });
  form.addEventListener('submit', (event) => {
    try {
      const payloadForSubmit = collectFormPayload();
      document.getElementById('json_input').value = JSON.stringify(payloadForSubmit);
    } catch (error) {
      event.preventDefault();
      setStatus(error && error.message ? error.message : 'Проверьте поля формы.', true);
    }
  });
}

// Plotly chart rendering
window.renderForecastCharts = function (forecast) {
  try {
    const series = forecast.series || {};
    const lat = series.latency_vs_rps || [];
    const util = series.util_vs_rps || [];
    const inst = series.instances_vs_rps || [];
    const targetRps = forecast.targets ? forecast.targets.rps : null;
    const latencyRps = lat.map((p) => roundOrNull(p.rps, 1));
    const utilRps = util.map((p) => roundOrNull(p.rps, 1));
    const instRps = inst.map((p) => roundOrNull(p.rps, 1));
    let slo = null;
    if (forecast.meta && forecast.meta.slo_ms_max_optional) {
      const n = parseFloat(forecast.meta.slo_ms_max_optional);
      if (!Number.isNaN(n) && n > 0) slo = n;
    }
    const uMaxRam = forecast.meta && forecast.meta.u_max_ram ? parseFloat(forecast.meta.u_max_ram) : null;
    const uMaxCpu = forecast.meta && forecast.meta.u_max_cpu ? parseFloat(forecast.meta.u_max_cpu) : null;
    const primaryMaxFieldByModel = {
      'G/G/c': 'g_g_c_max_ms',
      'M/M/c': 'm_m_c_max_ms',
      'Kingman G/G/1': 'kingman_max_ms',
      'M/M/1': 'm_m_1_max_ms',
    };
    const primaryMaxField = forecast.meta ? primaryMaxFieldByModel[forecast.meta.primary_model] : null;
    const primaryMaxValues = lat.map((p) => (primaryMaxField ? roundOrNull(p[primaryMaxField], 1) : null));
    const observedMaxValues = lat.map((p) => roundOrNull(p.observed_max_ms, 1));
    const latencyMaxY = maxFinite(primaryMaxValues.concat(observedMaxValues, [slo]), 1);

    const decisionLatencyTraces = [
      {
        x: latencyRps,
        y: observedMaxValues,
        name: 'Факт max',
        mode: 'markers',
        marker: {color: '#444', size: 7},
        hovertemplate: 'RPS %{x:.0f}<br>Факт max %{y:,.1f} мс<extra></extra>',
      },
      {
        x: latencyRps,
        y: primaryMaxValues,
        name: 'Основной прогноз max',
        mode: 'lines',
        line: {color: '#2f855a', width: 3},
        hovertemplate: 'RPS %{x:.0f}<br>Прогноз max %{y:,.1f} мс<extra></extra>',
      },
    ];
    const layoutLat = {
      xaxis: {title: 'Нагрузка, RPS'},
      yaxis: {title: 'Максимальная задержка, мс', tickformat: ',.0f'},
      hovermode: 'closest',
      shapes: [],
      annotations: [],
      legend: {orientation: 'h'},
    };
    if (slo) {
      layoutLat.shapes.push(
        {type: 'rect', x0: 0, x1: 1, y0: slo, y1: latencyMaxY * 1.12, xref: 'paper', yref: 'y', fillcolor: 'rgba(229, 62, 62, 0.08)', line: {width: 0}},
        {type: 'line', x0: 0, x1: 1, y0: slo, y1: slo, xref: 'paper', yref: 'y', line: {dash: 'dot', color: 'red'}}
      );
      layoutLat.annotations.push({x: 1, xref: 'paper', y: slo, text: 'SLO', showarrow: false, xanchor: 'right'});
      const crossing = firstCrossing(lat, (p) => (primaryMaxField ? finiteOrNull(p[primaryMaxField]) : null), slo);
      if (crossing) {
        layoutLat.shapes.push({type: 'line', x0: crossing.rps, x1: crossing.rps, y0: 0, y1: 1, xref: 'x', yref: 'paper', line: {dash: 'dash', color: '#e53e3e'}});
        layoutLat.annotations.push({x: crossing.rps, y: 0.9, yref: 'paper', text: `Риск SLO с ${Math.round(crossing.rps)} RPS`, showarrow: false, xanchor: 'left'});
      }
    }
    if (targetRps) {
      layoutLat.shapes.push({type: 'line', x0: targetRps, x1: targetRps, y0: 0, y1: 1, xref: 'x', yref: 'paper', line: {dash: 'dot', color: '#999'}});
      layoutLat.annotations.push({
        x: targetRps,
        y: 1,
        yref: 'paper',
        text: forecast.meta && forecast.meta.primary_max_ms != null ? `target: ${forecast.meta.primary_max_ms.toFixed(1)} мс` : 'target RPS',
        showarrow: false,
        xanchor: 'left',
      });
    }
    Plotly.newPlot('chart-latency', decisionLatencyTraces, layoutLat, {displayModeBar: false, responsive: true});

    const cpuHeadroom = util.map((p) => (typeof p.cpu === 'number' && Number.isFinite(p.cpu) && uMaxCpu !== null ? roundOrNull((uMaxCpu - p.cpu) * 100, 1) : null));
    const ramHeadroom = util.map((p) => (typeof p.ram === 'number' && Number.isFinite(p.ram) && uMaxRam !== null ? roundOrNull((uMaxRam - p.ram) * 100, 1) : null));
    const cpuHeadroomDetails = util.map((p) => [
      typeof p.cpu === 'number' && Number.isFinite(p.cpu) ? roundOrNull(p.cpu * 100, 1) : null,
      uMaxCpu !== null ? roundOrNull(uMaxCpu * 100, 1) : null,
      typeof p.cpu === 'number' && Number.isFinite(p.cpu) && uMaxCpu !== null ? roundOrNull((p.cpu / uMaxCpu) * 100, 1) : null,
      typeof p.cpu === 'number' && Number.isFinite(p.cpu) && uMaxCpu !== null ? roundOrNull((uMaxCpu - p.cpu) * 100, 1) : null,
    ]);
    const ramHeadroomDetails = util.map((p) => [
      typeof p.ram === 'number' && Number.isFinite(p.ram) ? roundOrNull(p.ram * 100, 1) : null,
      uMaxRam !== null ? roundOrNull(uMaxRam * 100, 1) : null,
      typeof p.ram === 'number' && Number.isFinite(p.ram) && uMaxRam !== null ? roundOrNull((p.ram / uMaxRam) * 100, 1) : null,
      typeof p.ram === 'number' && Number.isFinite(p.ram) && uMaxRam !== null ? roundOrNull((uMaxRam - p.ram) * 100, 1) : null,
    ]);
    const layoutUtil = {
      xaxis: {title: 'Нагрузка, RPS'},
      yaxis: {title: 'Запас до порога, %'},
      hovermode: 'closest',
      shapes: [{type: 'line', x0: 0, x1: 1, y0: 0, y1: 0, xref: 'paper', yref: 'y', line: {dash: 'dot', color: '#666'}}],
      annotations: [{x: 1, xref: 'paper', y: 0, text: 'порог', showarrow: false, xanchor: 'right'}],
      legend: {orientation: 'h'},
    };
    const cpuBreach = firstBelow(util, (p) => (typeof p.cpu === 'number' && uMaxCpu !== null ? (uMaxCpu - p.cpu) * 100 : null), 0);
    if (cpuBreach) {
      layoutUtil.shapes.push({type: 'line', x0: cpuBreach.rps, x1: cpuBreach.rps, y0: 0, y1: 1, xref: 'x', yref: 'paper', line: {dash: 'dash', color: '#ed8936'}});
      layoutUtil.annotations.push({x: cpuBreach.rps, y: 0.9, yref: 'paper', text: `CPU лимит: ${Math.round(cpuBreach.rps)} RPS`, showarrow: false, xanchor: 'left'});
    }
    const ramBreach = firstBelow(util, (p) => (typeof p.ram === 'number' && uMaxRam !== null ? (uMaxRam - p.ram) * 100 : null), 0);
    if (ramBreach) {
      layoutUtil.shapes.push({type: 'line', x0: ramBreach.rps, x1: ramBreach.rps, y0: 0, y1: 1, xref: 'x', yref: 'paper', line: {dash: 'dash', color: '#805ad5'}});
      layoutUtil.annotations.push({x: ramBreach.rps, y: 0.78, yref: 'paper', text: `RAM лимит: ${Math.round(ramBreach.rps)} RPS`, showarrow: false, xanchor: 'left'});
    }
    if (targetRps) {
      layoutUtil.shapes.push({type: 'line', x0: targetRps, x1: targetRps, y0: 0, y1: 1, xref: 'x', yref: 'paper', line: {dash: 'dot', color: '#999'}});
      layoutUtil.annotations.push({x: targetRps, y: 1, yref: 'paper', text: 'target RPS', showarrow: false, xanchor: 'left'});
    }
    Plotly.newPlot('chart-util', [
      {
        x: utilRps,
        y: cpuHeadroom,
        customdata: cpuHeadroomDetails,
        mode: 'lines',
        name: 'CPU запас',
        line: {color: '#ed8936', width: 3},
        hovertemplate: 'RPS %{x:.0f}<br>CPU запас %{y:+.1f}%<br>CPU util %{customdata[0]:.1f}%<br>Порог %{customdata[1]:.1f}%<br>Использовано лимита %{customdata[2]:.1f}%<extra></extra>',
      },
      {
        x: utilRps,
        y: ramHeadroom,
        customdata: ramHeadroomDetails,
        mode: 'lines',
        name: 'RAM запас',
        line: {color: '#805ad5', width: 3},
        hovertemplate: 'RPS %{x:.0f}<br>RAM запас %{y:+.1f}%<br>RAM util %{customdata[0]:.1f}%<br>Порог %{customdata[1]:.1f}%<br>Использовано лимита %{customdata[2]:.1f}%<extra></extra>',
      },
    ], layoutUtil, {displayModeBar: false, responsive: true});

    const recommendedInstances = inst.map((p) => Math.max(p.instances_cpu || 0, p.instances_ram || 0));
    const recommendedInstanceDetails = inst.map((p) => [p.instances_cpu || 0, p.instances_ram || 0]);
    const recommendedTarget = forecast.meta ? forecast.meta.recommended_replicas : maxFinite(recommendedInstances, 1);
    const layoutInst = {
      xaxis: {title: 'Нагрузка, RPS'},
      yaxis: {title: 'Реплики', dtick: 1},
      shapes: [],
      annotations: [],
      legend: {orientation: 'h'},
    };
    if (recommendedTarget) {
      layoutInst.shapes.push({type: 'line', x0: 0, x1: 1, y0: recommendedTarget, y1: recommendedTarget, xref: 'paper', yref: 'y', line: {dash: 'dot', color: '#2f855a'}});
      layoutInst.annotations.push({x: 1, xref: 'paper', y: recommendedTarget, text: `рекомендация: ${recommendedTarget}`, showarrow: false, xanchor: 'right'});
    }
    if (targetRps) {
      layoutInst.shapes.push({type: 'line', x0: targetRps, x1: targetRps, y0: 0, y1: 1, xref: 'x', yref: 'paper', line: {dash: 'dot', color: '#999'}});
      layoutInst.annotations.push({x: targetRps, y: 1, yref: 'paper', text: 'target RPS', showarrow: false, xanchor: 'left'});
    }
    Plotly.newPlot('chart-instances', [
      {
        x: instRps,
        y: recommendedInstances,
        customdata: recommendedInstanceDetails,
        mode: 'lines',
        name: 'Рекомендуемые реплики',
        line: {color: '#2f855a', width: 3, shape: 'hv'},
        hovertemplate: 'RPS %{x:.0f}<br>Рекомендуемые реплики %{y:.0f}<br>CPU-based %{customdata[0]:.0f}<br>RAM-based %{customdata[1]:.0f}<extra></extra>',
      },
    ], layoutInst, {displayModeBar: false, responsive: true});

    const latencyTraces = [
      {
        x: latencyRps,
        y: lat.map((p) => roundOrNull(p.observed_avg_ms, 1)),
        name: 'Observed avg',
        mode: 'markers',
        marker: {color: '#444'},
      },
      {
        x: latencyRps,
        y: observedMaxValues,
        name: 'Observed max',
        mode: 'markers',
        marker: {color: '#888'},
      },
    ];
    [
      ['m_m_1_avg_ms', 'M/M/1 avg', '#2b6cb0'],
      ['m_m_c_avg_ms', 'M/M/c avg', '#d97706'],
      ['kingman_avg_ms', 'G/G/1 avg', '#805ad5'],
      ['g_g_c_avg_ms', 'G/G/c avg', '#2f855a'],
    ].forEach(([field, name, color]) => {
      const y = lat.map((p) => roundOrNull(p[field], 1));
      if (hasNumericValues(y)) {
        latencyTraces.push({
          x: latencyRps,
          y,
          name,
          mode: 'lines',
          line: {color},
          hovertemplate: `RPS %{x:.0f}<br>${name} %{y:,.1f} мс<extra></extra>`,
        });
      }
    });
    const layoutEngineeringLat = {
      xaxis: {title: 'Нагрузка, RPS'},
      yaxis: {title: 'Задержка, мс', tickformat: ',.0f'},
      hovermode: 'closest',
      shapes: [],
      annotations: [],
      legend: {orientation: 'h'},
    };
    if (targetRps) {
      layoutEngineeringLat.shapes.push({
        type: 'line', x0: targetRps, x1: targetRps, y0: 0, y1: 1,
        xref: 'x', yref: 'paper', line: {dash: 'dot', color: '#999'},
      });
      layoutEngineeringLat.annotations.push({
        x: targetRps, y: 1, yref: 'paper',
        text: 'target RPS', showarrow: false, xanchor: 'left',
      });
    }
    if (slo) {
      layoutEngineeringLat.shapes.push({
        type: 'line', x0: 0, x1: 1, y0: slo, y1: slo,
        xref: 'paper', yref: 'y', line: {dash: 'dot', color: 'red'},
      });
      layoutEngineeringLat.annotations.push({
        x: 1, xref: 'paper', y: slo,
        text: 'SLO', showarrow: false, xanchor: 'right',
      });
    }
    if (document.getElementById('chart-engineering-latency')) {
      Plotly.newPlot('chart-engineering-latency', latencyTraces, layoutEngineeringLat, {displayModeBar: false, responsive: true});
    }

    const layoutEngineeringUtil = {xaxis: {title: 'Нагрузка, RPS'}, yaxis: {title: 'Утилизация (0..1)'}, shapes: [], annotations: [], legend: {orientation: 'h'}};
    if (!Number.isNaN(uMaxCpu) && uMaxCpu !== null) {
      layoutEngineeringUtil.shapes.push({type: 'line', x0: 0, x1: 1, y0: uMaxCpu, y1: uMaxCpu, xref: 'paper', yref: 'y', line: {dash: 'dot', color: '#ed8936'}});
      layoutEngineeringUtil.annotations.push({x: 1, xref: 'paper', y: uMaxCpu, text: 'u_max_cpu', showarrow: false, xanchor: 'right'});
    }
    if (!Number.isNaN(uMaxRam) && uMaxRam !== null) {
      layoutEngineeringUtil.shapes.push({type: 'line', x0: 0, x1: 1, y0: uMaxRam, y1: uMaxRam, xref: 'paper', yref: 'y', line: {dash: 'dot', color: '#805ad5'}});
      layoutEngineeringUtil.annotations.push({x: 1, xref: 'paper', y: uMaxRam, text: 'u_max_ram', showarrow: false, xanchor: 'right'});
    }
    if (targetRps) {
      layoutEngineeringUtil.shapes.push({type: 'line', x0: targetRps, x1: targetRps, y0: 0, y1: 1, xref: 'x', yref: 'paper', line: {dash: 'dot', color: '#999'}});
      layoutEngineeringUtil.annotations.push({x: targetRps, y: 1, yref: 'paper', text: 'target RPS', showarrow: false, xanchor: 'left'});
    }
    if (document.getElementById('chart-engineering-util')) {
      Plotly.newPlot('chart-engineering-util', [
        {
          x: utilRps,
          y: util.map((p) => roundOrNull(p.cpu, 3)),
          customdata: cpuHeadroomDetails,
          mode: 'lines',
          name: 'CPU',
          line: {color: '#ed8936'},
          hovertemplate: 'RPS %{x:.0f}<br>CPU util %{customdata[0]:.1f}%<br>Порог %{customdata[1]:.1f}%<br>Запас %{customdata[3]:+.1f}%<br>Использовано лимита %{customdata[2]:.1f}%<extra></extra>',
        },
        {
          x: utilRps,
          y: util.map((p) => roundOrNull(p.ram, 3)),
          customdata: ramHeadroomDetails,
          mode: 'lines',
          name: 'RAM',
          line: {color: '#805ad5'},
          hovertemplate: 'RPS %{x:.0f}<br>RAM util %{customdata[0]:.1f}%<br>Порог %{customdata[1]:.1f}%<br>Запас %{customdata[3]:+.1f}%<br>Использовано лимита %{customdata[2]:.1f}%<extra></extra>',
        },
      ], layoutEngineeringUtil, {displayModeBar: false, responsive: true});
    }

    const layoutEngineeringInst = {xaxis: {title: 'Нагрузка, RPS'}, yaxis: {title: 'Реплики', dtick: 1}, legend: {orientation: 'h'}};
    if (document.getElementById('chart-engineering-instances')) {
      Plotly.newPlot('chart-engineering-instances', [
        {
          x: instRps,
          y: inst.map((p) => p.instances_cpu),
          customdata: recommendedInstanceDetails,
          mode: 'lines',
          name: 'CPU',
          line: {shape: 'hv'},
          hovertemplate: 'RPS %{x:.0f}<br>CPU-based реплики %{y:.0f}<br>Итоговая рекомендация %{customdata[0]:.0f}/%{customdata[1]:.0f} CPU/RAM<extra></extra>',
        },
        {
          x: instRps,
          y: inst.map((p) => p.instances_ram),
          customdata: recommendedInstanceDetails,
          mode: 'lines',
          name: 'RAM',
          line: {shape: 'hv'},
          hovertemplate: 'RPS %{x:.0f}<br>RAM-based реплики %{y:.0f}<br>Итоговая рекомендация %{customdata[0]:.0f}/%{customdata[1]:.0f} CPU/RAM<extra></extra>',
        },
      ], layoutEngineeringInst, {displayModeBar: false, responsive: true});
    }
    document.getElementById('engineering-charts')?.addEventListener('shown.bs.collapse', () => {
      ['chart-engineering-latency', 'chart-engineering-util', 'chart-engineering-instances'].forEach((id) => {
        const element = document.getElementById(id);
        if (element) Plotly.Plots.resize(element);
      });
    }, {once: true});
  } catch (e) {
    console.error(e);
  }
};

document.addEventListener('DOMContentLoaded', initStepForm);
