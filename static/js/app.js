// Plotly chart rendering
window.renderForecastCharts = function (forecast, meta) {
  try {
    const series = forecast.series || {};
    const lat = series.latency_vs_rps || [];
    const util = series.util_vs_rps || [];
    const inst = series.instances_vs_rps || [];
    let slo = null;
    if (forecast.meta && forecast.meta.slo_ms_max_optional) {
      const n = parseFloat(forecast.meta.slo_ms_max_optional);
      if (!isNaN(n) && n > 0) slo = n;
    }
    const uMaxRam = (forecast.meta && forecast.meta.u_max_ram) ? parseFloat(forecast.meta.u_max_ram) : null;
    const uMaxCpu = (forecast.meta && forecast.meta.u_max_cpu) ? parseFloat(forecast.meta.u_max_cpu) : null;
    // Latency chart
    const traceObsAvg = {
      x: lat.map(p => p.rps),
      y: lat.map(p => p.observed_avg_ms),
      name: 'Observed avg',
      mode: 'markers',
      marker: {color: '#444'}
    };
    const traceObsMax = {
      x: lat.map(p => p.rps),
      y: lat.map(p => p.observed_max_ms),
      name: 'Observed max',
      mode: 'markers',
      marker: {color: '#888'}
    };
    const traceMM1 = {
      x: lat.map(p => p.rps),
      y: lat.map(p => p.m_m_1_avg_ms),
      name: 'M/M/1 avg',
      mode: 'lines',
      line: {color: '#2b6cb0'}
    };
    // G/G/c (Allen–Cunneen)
    const traceGGC = {
      x: lat.map(p => p.rps),
      y: lat.map(p => (typeof p.g_g_c_avg_ms === 'number' && isFinite(p.g_g_c_avg_ms)) ? p.g_g_c_avg_ms : null),
      name: 'G/G/c avg',
      mode: 'lines',
      line: {color: '#2f855a'}
    };
    // Add M/M/c and G/G/1 traces (skip non-finite values)
    const traceMMC = {
      x: lat.map(p => p.rps),
      y: lat.map(p => (typeof p.m_m_c_avg_ms === 'number' && isFinite(p.m_m_c_avg_ms)) ? p.m_m_c_avg_ms : null),
      name: 'M/M/c avg',
      mode: 'lines',
      line: {color: '#d97706'}
    };
    const traceKing = {
      x: lat.map(p => p.rps),
      y: lat.map(p => (typeof p.kingman_avg_ms === 'number' && isFinite(p.kingman_avg_ms)) ? p.kingman_avg_ms : null),
      name: 'G/G/1 avg',
      mode: 'lines',
      line: {color: '#805ad5'}
    };
    const layoutLat = {
      xaxis: {title: 'RPS'},
      yaxis: {title: 'мс'},
      hovermode: 'closest',
      shapes: [],
      annotations: []
    };
    if (forecast.targets && forecast.targets.rps) {
      layoutLat.shapes.push({
        type: 'line', x0: forecast.targets.rps, x1: forecast.targets.rps, y0: 0, y1: 1,
        xref: 'x', yref: 'paper', line: {dash: 'dot', color: '#999'}
      });
      layoutLat.annotations.push({
        x: forecast.targets.rps, y: 1, yref: 'paper',
        text: 'target RPS', showarrow: false, xanchor: 'left'
      });
    }
    if (forecast.meta && forecast.meta.usl_peak_rps) {
      const xp = parseFloat(forecast.meta.usl_peak_rps);
      if (!isNaN(xp) && xp > 0) {
        layoutLat.shapes.push({
          type: 'line', x0: xp, x1: xp, y0: 0, y1: 1,
          xref: 'x', yref: 'paper', line: {dash: 'dash', color: '#e53e3e'}
        });
        layoutLat.annotations.push({
          x: xp, y: 0.9, yref: 'paper',
          text: 'Насыщение (USL)', showarrow: false, xanchor: 'left'
        });
      }
    }
    if (slo) {
      layoutLat.shapes.push({
        type: 'line', x0: 0, x1: 1, y0: slo, y1: slo,
        xref: 'paper', yref: 'y', line: {dash: 'dot', color: 'red'}
      });
      layoutLat.annotations.push({
        x: 1, xref: 'paper', y: slo,
        text: 'SLO', showarrow: false, xanchor: 'right'
      });
    }
    Plotly.newPlot('chart-latency', [traceObsAvg, traceObsMax, traceMM1, traceMMC, traceKing, traceGGC], layoutLat, {displayModeBar: false});

    // CPU/RAM Util chart with thresholds
    const layoutUtil = {xaxis: {title: 'RPS'}, yaxis: {title: 'Utilization (0..1)'}, shapes: [], annotations: []};
    if (!isNaN(uMaxCpu) && uMaxCpu !== null) {
      layoutUtil.shapes.push({type: 'line', x0: 0, x1: 1, y0: uMaxCpu, y1: uMaxCpu, xref: 'paper', yref: 'y', line: {dash: 'dot', color: '#ed8936'}});
      layoutUtil.annotations.push({x: 1, xref: 'paper', y: uMaxCpu, text: 'u_max_cpu', showarrow: false, xanchor: 'right'});
    }
    if (!isNaN(uMaxRam) && uMaxRam !== null) {
      layoutUtil.shapes.push({type: 'line', x0: 0, x1: 1, y0: uMaxRam, y1: uMaxRam, xref: 'paper', yref: 'y', line: {dash: 'dot', color: '#e53e3e'}});
      layoutUtil.annotations.push({x: 1, xref: 'paper', y: uMaxRam, text: 'u_max_ram', showarrow: false, xanchor: 'right'});
    }
    Plotly.newPlot('chart-util', [
      {x: util.map(p => p.rps), y: util.map(p => p.cpu), mode: 'lines', name: 'CPU', line: {color: '#2b6cb0'}},
      {x: util.map(p => p.rps), y: util.map(p => p.ram), mode: 'lines', name: 'RAM', line: {color: '#805ad5'}},
    ], layoutUtil, {displayModeBar: false});

    // Network MB/s chart with capacity lines
    if (forecast.series && forecast.series.net_vs_rps && document.getElementById('chart-net')) {
      const net = forecast.series.net_vs_rps;
      const xin = net.map(p => p.rps);
      const yIn = net.map(p => p.net_in_mbps);
      const yOut = net.map(p => p.net_out_mbps);
      const capIn = (forecast.network && forecast.network.cap_in_mbps) ? forecast.network.cap_in_mbps : null;
      const capOut = (forecast.network && forecast.network.cap_out_mbps) ? forecast.network.cap_out_mbps : null;
      const layoutNet = {xaxis: {title: 'RPS'}, yaxis: {title: 'MB/s'}, shapes: [], annotations: []};
      if (capIn) {
        layoutNet.shapes.push({type: 'line', x0: 0, x1: 1, y0: capIn, y1: capIn, xref: 'paper', yref: 'y', line: {dash: 'dot', color: '#718096'}});
        layoutNet.annotations.push({x: 1, xref: 'paper', y: capIn, text: 'cap_in', showarrow: false, xanchor: 'right'});
      }
      if (capOut) {
        layoutNet.shapes.push({type: 'line', x0: 0, x1: 1, y0: capOut, y1: capOut, xref: 'paper', yref: 'y', line: {dash: 'dot', color: '#a0aec0'}});
        layoutNet.annotations.push({x: 1, xref: 'paper', y: capOut, text: 'cap_out', showarrow: false, xanchor: 'right'});
      }
      Plotly.newPlot('chart-net', [
        {x: xin, y: yIn, mode: 'lines', name: 'Net In', line: {color: '#38a169'}},
        {x: xin, y: yOut, mode: 'lines', name: 'Net Out', line: {color: '#3182ce'}},
      ], layoutNet, {displayModeBar: false});
    }

    // Instances chart
    const layoutInst = {xaxis: {title: 'RPS'}, yaxis: {title: 'Instances'}};
    Plotly.newPlot('chart-instances', [
      {x: inst.map(p => p.rps), y: inst.map(p => p.instances_cpu), mode: 'lines', name: 'CPU'},
      {x: inst.map(p => p.rps), y: inst.map(p => p.instances_ram), mode: 'lines', name: 'RAM'},
    ], layoutInst, {displayModeBar: false});
  } catch (e) {
    // ignore rendering errors
    console.error(e);
  }
};


