import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from services.models import (
    mm1_response_time_ms,
    mmc_response_time_ms,
    erlang_c_wait_probability,
)
from services.schemas import (
    Capacity,
    ForecastMeta,
    ForecastOutput,
    InputSchema,
    LatencyPair,
    MMCOut,
    ModelsOut,
    SeriesInstancesPoint,
    SeriesLatencyPoint,
    SeriesOut,
    SeriesUtilPoint,
    TargetsInstances,
    TargetsLatency,
    TargetsOut,
    TargetsUtil,
    Variability,
)


@dataclass
class DerivedParams:
    D_cpu_s: float
    D_ram_s: float
    D_io_s: float
    S_ms: float
    Ca: float
    Cs: float
    baseline_ratio_max_to_avg: float
    mmc_c: int
    avg_cpu_request_m_per_pod: float
    avg_cpu_limit_m_per_pod: float
    avg_mem_request_mib_per_pod: float
    avg_mem_limit_mib_per_pod: float


def _fit_slope_through_origin(x: np.ndarray, y: np.ndarray) -> float:
    # slope minimizing ||y - b*x||_2
    if len(x) == 0 or len(y) == 0:
        return float("nan")
    denom = float(np.dot(x, x))
    if denom <= 0:
        return float("nan")
    return float(np.dot(x, y) / denom)


def _calc_variability(steps_df: pd.DataFrame) -> Tuple[float, float]:
    # Ca: coefficient of variation of rps between steps
    rps_vals = steps_df["rps"].to_numpy()
    Ca = float(np.std(rps_vals) / max(1e-9, np.mean(rps_vals)))
    # Cs: rough proxy from (max-avg)/avg per step; clamp
    ratio = (steps_df["max_ms"] - steps_df["avg_ms"]) / steps_df["avg_ms"]
    Cs_raw = float(np.mean(np.clip(ratio.to_numpy(), 0.0, 10.0)))
    Cs = float(np.clip(Cs_raw, 0.1, 2.0))
    return Ca, Cs


def _select_linear_region(steps_df: pd.DataFrame) -> pd.DataFrame:
    # Filter by low error rate and relative stability of avg_ms (<20% growth step-to-step)
    df = steps_df[steps_df["errors_pct"] <= 5.0].copy()
    if len(df) < 2:
        return steps_df.copy()
    df = df.sort_values("rps")
    # Keep consecutive steps where avg_ms growth is moderate
    keep_idx = [df.index[0]]
    prev = df.iloc[0]["avg_ms"]
    for idx, row in df.iloc[1:].iterrows():
        grow = (row["avg_ms"] - prev) / max(1e-9, prev)
        if grow <= 0.2:
            keep_idx.append(idx)
            prev = row["avg_ms"]
        else:
            break
    return df.loc[keep_idx]


def _derive_params(data: InputSchema) -> DerivedParams:
    # DataFrame for convenience
    steps = pd.DataFrame([s.model_dump() if hasattr(s, "model_dump") else s.dict() for s in data.steps])
    steps = steps.sort_values("rps").reset_index(drop=True)

    # Baseline ratio max/avg on first step
    baseline_ratio = float(steps.iloc[0]["max_ms"] / steps.iloc[0]["avg_ms"])

    # Derive effective utilizations relative to requests when possible
    def _clamp(v, lo, hi):
        return float(min(hi, max(lo, v)))

    cpu_utils = []
    ram_utils = []
    cpu_req_pp = []
    cpu_lim_pp = []
    mem_req_pp = []
    mem_lim_pp = []
    for _, row in steps.iterrows():
        pods = row.get("pods", None)
        # Always collect per-pod requests/limits for table if present
        if pods and row.get("cpu_request_m_per_pod") is not None:
            cpu_req_pp.append(float(row.get("cpu_request_m_per_pod") or 0.0))
        if pods and row.get("cpu_limit_m_per_pod") is not None:
            cpu_lim_pp.append(float(row.get("cpu_limit_m_per_pod") or 0.0))
        if pods and row.get("mem_request_mib_per_pod") is not None:
            mem_req_pp.append(float(row.get("mem_request_mib_per_pod") or 0.0))
        if pods and row.get("mem_limit_mib_per_pod") is not None:
            mem_lim_pp.append(float(row.get("mem_limit_mib_per_pod") or 0.0))
        # CPU utilization (prefer absolute usage normalized by requests, else legacy)
        if pods and row.get("cpu_request_m_per_pod") is not None and row.get("cpu_usage_m") is not None:
            total_req = float(pods) * float(row.get("cpu_request_m_per_pod", 0.0))
            u = 0.0
            if total_req > 0:
                u = _clamp(float(row.get("cpu_usage_m", 0.0)) / total_req, 0.0, 1.5)
            cpu_utils.append(u)
        else:
            cpu_utils.append(float(row.get("cpu_util", float("nan"))))
        # RAM utilization (prefer workingset normalized by requests, else legacy)
        if pods and row.get("mem_request_mib_per_pod") is not None and row.get("mem_workingset_mib") is not None:
            total_mem_req = float(pods) * float(row.get("mem_request_mib_per_pod", 0.0))
            u = 0.0
            if total_mem_req > 0:
                u = _clamp(float(row.get("mem_workingset_mib", 0.0)) / total_mem_req, 0.0, 1.5)
            ram_utils.append(u)
        else:
            ram_utils.append(float(row.get("ram_util", float("nan"))))
    steps["cpu_util"] = pd.Series(cpu_utils, index=steps.index)
    steps["ram_util"] = pd.Series(ram_utils, index=steps.index)

    # Linear region for device demands
    lin = _select_linear_region(steps)
    X = lin["rps"].to_numpy(dtype=float)
    # U = X * D  => slope = D
    D_cpu = _fit_slope_through_origin(X, lin["cpu_util"].to_numpy(dtype=float))
    D_ram = _fit_slope_through_origin(X, lin["ram_util"].to_numpy(dtype=float))
    D_io = _fit_slope_through_origin(
        X, lin["io_util"].to_numpy(dtype=float) if "io_util" in lin.columns else np.array([])
    )

    # Service time S ~= max(D_i)
    D_list = [v for v in [D_cpu, D_io] if np.isfinite(v) and v > 0]
    if not D_list:
        # Fallback: use smallest observed avg_ms as S approx at low load
        S_ms = float(steps.iloc[0]["avg_ms"])
        D_cpu = D_io = S_ms / 1000.0 / 3.0
        # RAM remains a separate capacity resource.
        if not (np.isfinite(D_ram) and D_ram > 0):
            D_ram = S_ms / 1000.0 / 3.0
    else:
        S_ms = max(D_list) * 1000.0

    Ca, Cs = _calc_variability(steps)

    c = int(data.capacity.mmc_c_optional or 1)

    return DerivedParams(
        D_cpu_s=float(D_cpu if np.isfinite(D_cpu) and D_cpu > 0 else 0.001),
        D_ram_s=float(D_ram if np.isfinite(D_ram) and D_ram > 0 else 0.001),
        D_io_s=float(D_io if np.isfinite(D_io) and D_io > 0 else 0.001),
        S_ms=float(S_ms),
        Ca=Ca,
        Cs=Cs,
        baseline_ratio_max_to_avg=baseline_ratio,
        mmc_c=c,
        avg_cpu_request_m_per_pod=float(np.nanmean(cpu_req_pp) if cpu_req_pp else 0.0),
        avg_cpu_limit_m_per_pod=float(np.nanmean(cpu_lim_pp) if cpu_lim_pp else 0.0),
        avg_mem_request_mib_per_pod=float(np.nanmean(mem_req_pp) if mem_req_pp else 0.0),
        avg_mem_limit_mib_per_pod=float(np.nanmean(mem_lim_pp) if mem_lim_pp else 0.0),
    )


def _kingman_R_ms(X: float, S_ms: float, Ca: float, Cs: float) -> float:
    S_s = S_ms / 1000.0
    rho = X * S_s
    if rho >= 1.0:
        return float("inf")
    Wq = (rho / (1.0 - rho)) * ((Ca * Ca + Cs * Cs) / 2.0) * S_s
    return (S_s + Wq) * 1000.0


def _ggc_response_time_ms(X: float, S_ms: float, Ca: float, Cs: float, c: int) -> float:
    S_s = S_ms / 1000.0
    mu = 1.0 / S_s
    lam = X
    a = lam / mu
    denom = c * mu - lam
    if denom <= 0:
        return float("inf")
    Pw = erlang_c_wait_probability(a, c)
    Wq_mmc_s = Pw / denom
    Wq_ggc_s = ((Ca * Ca + Cs * Cs) / 2.0) * Wq_mmc_s
    return (S_s + Wq_ggc_s) * 1000.0


def _utilizations(X: float, d: DerivedParams) -> Tuple[float, float, float]:
    return X * d.D_cpu_s, X * d.D_ram_s, X * d.D_io_s


def _instances_needed(u: float, u_max: float) -> int:
    if u_max <= 0:
        return 1
    import math as _m

    return max(1, int(_m.ceil(u / u_max)))


def _suggest_m_for_slo(
    X: float, S_ms: float, start_c: int, slo_ms: float
) -> Tuple[int, float]:
    c = max(1, start_c)
    best_c = c
    R_ms, Pw = mmc_response_time_ms(S_ms, X, c)
    if R_ms <= slo_ms:
        return c, R_ms
    for c_try in range(c + 1, c + 16):
        R_try, _ = mmc_response_time_ms(S_ms, X, c_try)
        if R_try <= slo_ms:
            return c_try, R_try
        best_c = c_try if R_try < R_ms else best_c
    return best_c, R_ms


def compute_forecast(data: InputSchema) -> ForecastOutput:
    # Filter out too erroneous steps for observed series.
    steps = [s for s in data.steps if s.errors_pct <= 5.0]
    if len(steps) < 2:
        raise ValueError("Фильтр steps: недостаточно данных (исключены все с errors_pct >5%)")

    params = _derive_params(data)
    X_target = float(data.target.target_rps)
    slo_ms = float(data.target.slo_ms_max_optional or 0.0)

    # Ratios
    k_peak = params.baseline_ratio_max_to_avg
    modeling = data.modeling

    # Target latencies per model
    m_m_1_avg = mm1_response_time_ms(params.S_ms, X_target) if modeling.use_m_m_1 else None
    m_m_1_max = m_m_1_avg * k_peak if m_m_1_avg is not None else None

    c = params.mmc_c
    if modeling.use_m_m_c:
        m_m_c_avg, wait_prob = mmc_response_time_ms(params.S_ms, X_target, c)
        m_m_c_max = m_m_c_avg * k_peak
    else:
        m_m_c_avg = None
        m_m_c_max = None
        wait_prob = 0.0

    kingman_avg = _kingman_R_ms(X_target, params.S_ms, params.Ca, params.Cs) if modeling.use_kingman else None
    kingman_max = kingman_avg * k_peak if kingman_avg is not None else None

    # G/G/c (Allen–Cunneen) at target
    if modeling.use_g_g_c:
        ggc_avg_ms = _ggc_response_time_ms(X_target, params.S_ms, params.Ca, params.Cs, c)
        ggc_max_ms = ggc_avg_ms * k_peak
    else:
        ggc_avg_ms = None
        ggc_max_ms = None

    # Utilizations at target
    u_cpu, u_ram, u_io = _utilizations(X_target, params)

    cap: Capacity = data.capacity
    u_max_cpu = float(cap.u_max_cpu)
    u_max_ram = float(cap.u_max_ram)
    u_max_io = float(cap.u_max_io_optional or 1.0)
    inst_cpu = _instances_needed(u_cpu, u_max_cpu)
    inst_ram = _instances_needed(u_ram, u_max_ram)
    inst_io = _instances_needed(u_io, u_max_io)
    cpu_based = max(inst_cpu, 1)
    ram_based = max(inst_ram, 1)

    # Suggested m for M/M/c to meet SLO if given.
    suggested_c = c
    if slo_ms > 0.0:
        suggested_c, _ = _suggest_m_for_slo(X_target, params.S_ms, c, slo_ms)

    targets = TargetsOut(
        rps=X_target,
        latency_ms=TargetsLatency(
            m_m_1=LatencyPair(avg=m_m_1_avg, max=m_m_1_max) if m_m_1_avg is not None and m_m_1_max is not None else None,
            m_m_c=LatencyPair(avg=m_m_c_avg, max=m_m_c_max) if m_m_c_avg is not None and m_m_c_max is not None else None,
            kingman=LatencyPair(avg=kingman_avg, max=kingman_max) if kingman_avg is not None and kingman_max is not None else None,
            g_g_c=LatencyPair(avg=ggc_avg_ms, max=ggc_max_ms) if ggc_avg_ms is not None and ggc_max_ms is not None else None,
        ),
        utilization=TargetsUtil(cpu=u_cpu, ram=u_ram, io=u_io),
        instances=TargetsInstances(cpu_based=cpu_based, ram_based=ram_based, suggested_m=int(max(cpu_based, ram_based, suggested_c))),
    )

    models = ModelsOut(
        service_time_ms=float(params.S_ms),
        variability=Variability(Ca=params.Ca, Cs=params.Cs),
        mmc=MMCOut(c=int(c), wait_prob=float(wait_prob)),
        kube={
            "cpu_request_m_per_pod": params.avg_cpu_request_m_per_pod,
            "cpu_limit_m_per_pod": params.avg_cpu_limit_m_per_pod,
            "mem_request_mib_per_pod": params.avg_mem_request_mib_per_pod,
            "mem_limit_mib_per_pod": params.avg_mem_limit_mib_per_pod,
        },
    )

    # Series generation
    min_rps_obs = min(s.rps for s in steps)
    max_rps_obs = max(s.rps for s in steps)
    r_start = float(min_rps_obs)
    r_end = float(max(2.0 * X_target, max_rps_obs * 1.2))
    base_grid = np.linspace(r_start, r_end, num=50).tolist()
    # Include exact observed rps into grid to render markers
    observed_rps = sorted({float(s.rps) for s in steps})
    grid = sorted({float(round(x, 6)) for x in (base_grid + observed_rps)})

    # Observed dicts with tolerance lookup
    observed_map = {float(s.rps): s for s in steps}

    series_latency: List[SeriesLatencyPoint] = []
    series_util: List[SeriesUtilPoint] = []
    series_instances: List[SeriesInstancesPoint] = []

    for r in grid:
        # Predicted by models
        mm1_avg = mm1_response_time_ms(params.S_ms, r) if modeling.use_m_m_1 else None
        mm1_max = mm1_avg * k_peak if mm1_avg is not None else None
        if modeling.use_m_m_c:
            mmc_avg, _ = mmc_response_time_ms(params.S_ms, r, c)
            mmc_max = mmc_avg * k_peak
        else:
            mmc_avg = None
            mmc_max = None
        k_avg = _kingman_R_ms(r, params.S_ms, params.Ca, params.Cs) if modeling.use_kingman else None
        k_max = k_avg * k_peak if k_avg is not None else None
        # G/G/c series point
        if modeling.use_g_g_c:
            ggc_avg_r = _ggc_response_time_ms(r, params.S_ms, params.Ca, params.Cs, c)
            ggc_max_r = ggc_avg_r * k_peak
        else:
            ggc_avg_r = None
            ggc_max_r = None

        # Observed if exact step
        obs = observed_map.get(float(round(r, 6)))
        obs_avg = float(obs.avg_ms) if obs else None
        obs_max = float(obs.max_ms) if obs else None

        series_latency.append(
            SeriesLatencyPoint(
                rps=float(r),
                observed_avg_ms=obs_avg,
                observed_max_ms=obs_max,
                m_m_1_avg_ms=mm1_avg,
                m_m_1_max_ms=mm1_max,
                m_m_c_avg_ms=mmc_avg,
                m_m_c_max_ms=mmc_max,
                kingman_avg_ms=k_avg,
                kingman_max_ms=k_max,
                g_g_c_avg_ms=ggc_avg_r,
                g_g_c_max_ms=ggc_max_r,
            )
        )

        # Utilizations and instances
        u_cpu_r, u_ram_r, u_io_r = _utilizations(r, params)
        series_util.append(
            SeriesUtilPoint(
                rps=float(r),
                cpu=u_cpu_r,
                ram=u_ram_r,
                io=u_io_r,
            )
        )
        series_instances.append(
            SeriesInstancesPoint(
                rps=float(r),
                instances_cpu=_instances_needed(u_cpu_r, u_max_cpu),
                instances_ram=_instances_needed(u_ram_r, u_max_ram),
                instances_io=_instances_needed(u_io_r, u_max_io),
            )
        )

    series = SeriesOut(
        latency_vs_rps=series_latency, util_vs_rps=series_util, instances_vs_rps=series_instances
    )

    # Meta summary for UI
    num_steps = len(data.steps)
    # Compute actual linear region boundaries for summary
    try:
        steps_df_all = pd.DataFrame(
            [s.model_dump() if hasattr(s, "model_dump") else s.dict() for s in data.steps]
        ).sort_values("rps").reset_index(drop=True)
        lin_df = _select_linear_region(steps_df_all)
        if "step" in lin_df.columns and len(lin_df) >= 1:
            lin_ids = [str(x) for x in lin_df["step"].tolist()]
            if len(lin_ids) >= 2:
                linear_steps = f"{lin_ids[0]}–{lin_ids[-1]}"
            else:
                linear_steps = lin_ids[0]
        else:
            linear_steps = "?"
    except Exception:
        linear_steps = "?"
    slo_text = ""
    if data.target.slo_ms_max_optional:
        slo_text = f"SLO={int(data.target.slo_ms_max_optional)} мс"
    avg_candidates = [
        value
        for value in (m_m_1_avg, kingman_avg, ggc_avg_ms, m_m_c_avg)
        if value is not None and math.isfinite(value)
    ]
    max_candidates = [
        value
        for value in (m_m_1_max, kingman_max, ggc_max_ms, m_m_c_max)
        if value is not None and math.isfinite(value)
    ]
    avg_range_text = (
        f"{min(avg_candidates):.1f}–{max(avg_candidates):.1f} мс"
        if avg_candidates
        else "н/д"
    )
    max_latency_text = f"{max(max_candidates):.1f} мс" if max_candidates else "н/д"
    cpu_pressure = u_cpu / max(1e-9, u_max_cpu)
    ram_pressure = u_ram / max(1e-9, u_max_ram)
    if ram_pressure >= cpu_pressure:
        bottleneck = "RAM"
        bottleneck_pressure = ram_pressure
    else:
        bottleneck = "CPU"
        bottleneck_pressure = cpu_pressure

    primary_model = "n/a"
    primary_avg_ms = None
    primary_max_ms = None
    for model_name, avg_value, max_value in (
        ("G/G/c", ggc_avg_ms, ggc_max_ms),
        ("M/M/c", m_m_c_avg, m_m_c_max),
        ("Kingman G/G/1", kingman_avg, kingman_max),
        ("M/M/1", m_m_1_avg, m_m_1_max),
    ):
        if (
            avg_value is not None
            and max_value is not None
            and math.isfinite(avg_value)
            and math.isfinite(max_value)
        ):
            primary_model = model_name
            primary_avg_ms = avg_value
            primary_max_ms = max_value
            break

    slo_margin_ms = None
    if slo_ms > 0.0 and primary_max_ms is not None and math.isfinite(primary_max_ms):
        slo_margin_ms = slo_ms - primary_max_ms
        slo_status = "ok" if slo_margin_ms >= 0 else "risk"
    elif slo_ms > 0.0:
        slo_status = "risk"
    else:
        slo_status = "unknown"

    max_observed_rps = float(max_rps_obs)
    target_over_observed_ratio = X_target / max(1e-9, max_observed_rps)
    excluded_steps = num_steps - len(steps)
    quality_warnings: List[str] = []
    if len(steps) < 3:
        quality_warnings.append("Мало пригодных ступеней: прогноз чувствителен к шуму.")
    if excluded_steps > 0:
        quality_warnings.append(f"Исключено ступеней с errors_pct > 5%: {excluded_steps}.")
    if target_over_observed_ratio > 1.5:
        quality_warnings.append("Целевой RPS сильно выше наблюдаемого диапазона: экстраполяция рискованна.")
    if params.Ca > 1.0 or params.Cs > 1.5:
        quality_warnings.append("Высокая вариативность нагрузки или сервиса: ориентируйтесь на G/G/c.")
    if primary_model == "n/a":
        quality_warnings.append("Нет включённой основной latency-модели.")
    if not quality_warnings:
        quality_warnings.append("Критичных предупреждений по входным данным нет.")

    cpu_headroom_pct = (u_max_cpu - u_cpu) * 100.0
    ram_headroom_pct = (u_max_ram - u_ram) * 100.0

    summary = (
        f"Анализ на основе {num_steps} ступеней: Линейный участок — steps {linear_steps}. "
        f"Сервисное время S={params.S_ms:.1f} мс. На target_rps={X_target:.0f}: "
        f"Средняя задержка {avg_range_text}; "
        f"максимальная до {max_latency_text} "
        f"({slo_text}). Узкое место: {bottleneck} — масштабируйте на {targets.instances.suggested_m} инстанса."
    )

    out = ForecastOutput(
        targets=targets,
        models=models,
        series=series,
        meta=ForecastMeta(
            summary=summary,
            slo_ms_max_optional=str(int(slo_ms)) if slo_ms > 0 else "",
            u_max_ram=f"{u_max_ram:.2f}",
            u_max_cpu=f"{u_max_cpu:.2f}",
            observed_steps=num_steps,
            used_steps=len(steps),
            excluded_steps=excluded_steps,
            linear_steps=linear_steps,
            max_observed_rps=max_observed_rps,
            target_over_observed_ratio=target_over_observed_ratio,
            bottleneck=bottleneck,
            bottleneck_pressure=bottleneck_pressure,
            recommended_replicas=targets.instances.suggested_m,
            primary_model=primary_model,
            primary_avg_ms=primary_avg_ms,
            primary_max_ms=primary_max_ms,
            slo_status=slo_status,
            slo_margin_ms=slo_margin_ms,
            cpu_headroom_pct=cpu_headroom_pct,
            ram_headroom_pct=ram_headroom_pct,
            quality_warnings=quality_warnings,
        ),
    )
    return out


