import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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
    Step,
    TargetsInstances,
    TargetsLatency,
    TargetsOut,
    TargetsUtil,
    Variability,
)

INTERCEPT_EPS = 0.05
GROWTH_THRESHOLD = 0.2
VARIABILITY_NOTE = (
    "Ca — разброс RPS между ступенями; Cs — прокси tail latency (max-avg)/avg. "
    "Не являются классическими CV интервалов прибытия и сервисного времени."
)


@dataclass
class DerivedParams:
    D_cpu_s: float
    D_ram_s: float
    D_io_s: float
    D_cpu_intercept: float
    D_ram_intercept: float
    D_io_intercept: float
    S_ms: float
    Ca: float
    Cs: float
    baseline_ratio_max_to_avg: float
    mmc_c: int
    avg_cpu_request_m_per_pod: float
    avg_cpu_limit_m_per_pod: float
    avg_mem_request_mib_per_pod: float
    avg_mem_limit_mib_per_pod: float
    fit_warnings: List[str] = field(default_factory=list)
    pods_vary: bool = False


def _filter_usable_steps(steps: List[Step]) -> List[Step]:
    return [s for s in steps if s.errors_pct <= 5.0]


def _step_to_dict(step: Step) -> Dict:
    if hasattr(step, "model_dump"):
        return step.model_dump()
    return step.dict()  # type: ignore


def _fit_slope_with_intercept(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    if len(x) < 2 or len(y) < 2:
        return 0.0, float("nan")
    x_arr = x.astype(float)
    y_arr = y.astype(float)
    if not np.all(np.isfinite(x_arr)) or not np.all(np.isfinite(y_arr)):
        return 0.0, float("nan")
    slope, intercept = np.polyfit(x_arr, y_arr, 1)
    return float(intercept), float(slope)


def _median_util_per_rps(steps_df: pd.DataFrame, util_col: str) -> float:
    pods = steps_df["pods"].to_numpy(dtype=float) if "pods" in steps_df.columns else np.ones(len(steps_df))
    x = steps_df["rps"].to_numpy(dtype=float) / np.maximum(1.0, pods)
    y = steps_df[util_col].to_numpy(dtype=float)
    mask = np.isfinite(x) & np.isfinite(y) & (x > 0)
    if not np.any(mask):
        return float("nan")
    ratios = y[mask] / x[mask]
    return float(np.median(ratios))


def _resolve_slope(
    intercept: float,
    slope: float,
    steps_df: pd.DataFrame,
    util_col: str,
    label: str,
    warnings: List[str],
) -> Tuple[float, float]:
    if np.isfinite(slope) and slope > 0:
        return intercept, slope
    fallback = _median_util_per_rps(steps_df, util_col)
    if np.isfinite(fallback) and fallback > 0:
        warnings.append(
            f"Регрессия {label} нестабильна; использована медиана util/rps_per_pod={fallback:.4f}."
        )
        return 0.0, fallback
    warnings.append(f"Не удалось оценить спрос {label} по ступеням.")
    return 0.0, float("nan")


def _predict_observed_latency(
    rps_values: np.ndarray, latency_values: np.ndarray, target_rps: float
) -> float:
    """Interpolate observed latency vs RPS; extrapolate flat unless the last segment rises."""
    if len(rps_values) == 0 or len(latency_values) == 0:
        return float("nan")
    order = np.argsort(rps_values)
    x_sorted = rps_values[order].astype(float)
    y_sorted = latency_values[order].astype(float)
    unique_x: List[float] = []
    unique_y: List[float] = []
    for x_value in np.unique(x_sorted):
        mask = x_sorted == x_value
        unique_x.append(float(x_value))
        unique_y.append(float(np.mean(y_sorted[mask])))
    x = np.array(unique_x, dtype=float)
    y = np.array(unique_y, dtype=float)
    if len(x) == 1:
        return float(y[0])
    if target_rps <= x[-1]:
        return float(np.interp(target_rps, x, y))
    slope = (y[-1] - y[-2]) / max(1e-9, x[-1] - x[-2])
    if slope <= 0.0:
        return float(y[-1])
    return float(y[-1] + slope * (target_rps - x[-1]))


def _predict_empirical_pair(
    rps_values: np.ndarray,
    avg_values: np.ndarray,
    max_values: np.ndarray,
    target_rps: float,
) -> Tuple[float, float]:
    avg_ms = _predict_observed_latency(rps_values, avg_values, target_rps)
    max_ms = _predict_observed_latency(rps_values, max_values, target_rps)
    if math.isfinite(avg_ms) and math.isfinite(max_ms):
        max_ms = max(max_ms, avg_ms)
    return avg_ms, max_ms


def _aggregate_observed_by_rps(steps: List[Step]) -> Dict[float, Tuple[float, float]]:
    buckets: Dict[float, List[Step]] = {}
    for step in steps:
        key = float(step.rps)
        buckets.setdefault(key, []).append(step)
    aggregated: Dict[float, Tuple[float, float]] = {}
    for rps, group in buckets.items():
        aggregated[rps] = (
            float(np.mean([float(s.avg_ms) for s in group])),
            float(np.mean([float(s.max_ms) for s in group])),
        )
    return aggregated


def _calc_variability(steps_df: pd.DataFrame) -> Tuple[float, float]:
    rps_vals = steps_df["rps"].to_numpy()
    Ca = float(np.std(rps_vals) / max(1e-9, np.mean(rps_vals)))
    ratio = (steps_df["max_ms"] - steps_df["avg_ms"]) / steps_df["avg_ms"]
    Cs_raw = float(np.mean(np.clip(ratio.to_numpy(), 0.0, 10.0)))
    Cs = float(np.clip(Cs_raw, 0.1, 2.0))
    return Ca, Cs


def _relative_growth(current: float, previous: float) -> float:
    if not np.isfinite(current) or not np.isfinite(previous):
        return 0.0
    return (current - previous) / max(1e-9, previous)


def _select_linear_region(steps_df: pd.DataFrame) -> pd.DataFrame:
    df = steps_df.sort_values("rps").copy()
    if len(df) < 2:
        return df
    keep_idx = [df.index[0]]
    prev_avg = float(df.iloc[0]["avg_ms"])
    prev_cpu = float(df.iloc[0]["cpu_util"])
    prev_ram = float(df.iloc[0]["ram_util"])
    for idx, row in df.iloc[1:].iterrows():
        avg_grow = _relative_growth(float(row["avg_ms"]), prev_avg)
        cpu_grow = _relative_growth(float(row["cpu_util"]), prev_cpu)
        ram_grow = _relative_growth(float(row["ram_util"]), prev_ram)
        if avg_grow > GROWTH_THRESHOLD or cpu_grow > GROWTH_THRESHOLD or ram_grow > GROWTH_THRESHOLD:
            break
        keep_idx.append(idx)
        prev_avg = float(row["avg_ms"])
        prev_cpu = float(row["cpu_util"])
        prev_ram = float(row["ram_util"])
    return df.loc[keep_idx]


def _estimate_mmc_c(steps: List[Step], capacity: Capacity) -> int:
    if capacity.mmc_c_optional:
        return int(capacity.mmc_c_optional)
    channels: List[float] = []
    for step in steps:
        pods = float(step.pods or 1)
        if step.concurrency_optional is not None:
            channels.append(pods * float(step.concurrency_optional))
        else:
            channels.append(pods)
    return max(1, int(round(float(np.mean(channels)))))


def _build_steps_dataframe(usable_steps: List[Step]) -> pd.DataFrame:
    steps = pd.DataFrame([_step_to_dict(s) for s in usable_steps])
    steps = steps.sort_values("rps").reset_index(drop=True)

    def _clamp(v: float, lo: float, hi: float) -> float:
        return float(min(hi, max(lo, v)))

    cpu_utils: List[float] = []
    ram_utils: List[float] = []
    for _, row in steps.iterrows():
        pods = row.get("pods", None)
        if pods and row.get("cpu_request_m_per_pod") is not None and row.get("cpu_usage_m") is not None:
            pod_req = float(row.get("cpu_request_m_per_pod", 0.0))
            u = _clamp(float(row.get("cpu_usage_m", 0.0)) / pod_req, 0.0, 1.5) if pod_req > 0 else 0.0
            cpu_utils.append(u)
        else:
            cpu_utils.append(float(row.get("cpu_util", float("nan"))))
        if pods and row.get("mem_request_mib_per_pod") is not None and row.get("mem_workingset_mib") is not None:
            pod_mem_req = float(row.get("mem_request_mib_per_pod", 0.0))
            u = (
                _clamp(float(row.get("mem_workingset_mib", 0.0)) / pod_mem_req, 0.0, 1.5)
                if pod_mem_req > 0
                else 0.0
            )
            ram_utils.append(u)
        else:
            ram_utils.append(float(row.get("ram_util", float("nan"))))
    steps["cpu_util"] = pd.Series(cpu_utils, index=steps.index)
    steps["ram_util"] = pd.Series(ram_utils, index=steps.index)
    return steps


def _linear_steps_label(steps_df: pd.DataFrame) -> str:
    lin_df = _select_linear_region(steps_df)
    if "step" in lin_df.columns and len(lin_df) >= 1:
        lin_ids = [str(x) for x in lin_df["step"].tolist()]
        return f"{lin_ids[0]}–{lin_ids[-1]}" if len(lin_ids) >= 2 else lin_ids[0]
    return "?"


def _derive_params(data: InputSchema, usable_steps: List[Step]) -> DerivedParams:
    warnings: List[str] = []
    steps = _build_steps_dataframe(usable_steps)

    pods_values = steps["pods"].to_numpy(dtype=float) if "pods" in steps.columns else np.ones(len(steps))
    pods_vary = bool(len(pods_values) > 1 and np.std(pods_values) > 0)

    ratios = (steps["max_ms"] / steps["avg_ms"]).to_numpy(dtype=float)
    baseline_ratio = float(np.max(ratios))

    cpu_req_pp: List[float] = []
    cpu_lim_pp: List[float] = []
    mem_req_pp: List[float] = []
    mem_lim_pp: List[float] = []
    for _, row in steps.iterrows():
        pods = row.get("pods", None)
        if pods and row.get("cpu_request_m_per_pod") is not None:
            cpu_req_pp.append(float(row.get("cpu_request_m_per_pod") or 0.0))
        if pods and row.get("cpu_limit_m_per_pod") is not None:
            cpu_lim_pp.append(float(row.get("cpu_limit_m_per_pod") or 0.0))
        if pods and row.get("mem_request_mib_per_pod") is not None:
            mem_req_pp.append(float(row.get("mem_request_mib_per_pod") or 0.0))
        if pods and row.get("mem_limit_mib_per_pod") is not None:
            mem_lim_pp.append(float(row.get("mem_limit_mib_per_pod") or 0.0))

    lin = _select_linear_region(steps)
    pods_for_fit = lin["pods"].to_numpy(dtype=float) if "pods" in lin.columns else np.ones(len(lin))
    x = lin["rps"].to_numpy(dtype=float) / np.maximum(1.0, pods_for_fit)

    cpu_intercept, cpu_slope = _fit_slope_with_intercept(x, lin["cpu_util"].to_numpy(dtype=float))
    ram_intercept, ram_slope = _fit_slope_with_intercept(x, lin["ram_util"].to_numpy(dtype=float))
    if "io_util" in lin.columns and lin["io_util"].notna().any():
        io_intercept, io_slope = _fit_slope_with_intercept(
            x, lin["io_util"].to_numpy(dtype=float)
        )
    else:
        io_intercept, io_slope = 0.0, float("nan")

    cpu_intercept, cpu_slope = _resolve_slope(cpu_intercept, cpu_slope, steps, "cpu_util", "CPU", warnings)
    ram_intercept, ram_slope = _resolve_slope(ram_intercept, ram_slope, steps, "ram_util", "RAM", warnings)
    if np.isfinite(io_slope) and io_slope > 0:
        pass
    elif "io_util" in steps.columns and steps["io_util"].notna().any():
        io_intercept, io_slope = _resolve_slope(io_intercept, io_slope, steps, "io_util", "IO", warnings)
    else:
        io_intercept, io_slope = 0.0, 0.0

    S_ms = float(np.min(lin["avg_ms"].to_numpy(dtype=float)))
    Ca, Cs = _calc_variability(steps)
    mmc_c = _estimate_mmc_c(usable_steps, data.capacity)

    return DerivedParams(
        D_cpu_s=float(cpu_slope),
        D_ram_s=float(ram_slope if np.isfinite(ram_slope) else 0.0),
        D_io_s=float(io_slope if np.isfinite(io_slope) else 0.0),
        D_cpu_intercept=float(cpu_intercept),
        D_ram_intercept=float(ram_intercept),
        D_io_intercept=float(io_intercept),
        S_ms=S_ms,
        Ca=Ca,
        Cs=Cs,
        baseline_ratio_max_to_avg=baseline_ratio,
        mmc_c=mmc_c,
        avg_cpu_request_m_per_pod=float(np.nanmean(cpu_req_pp) if cpu_req_pp else 0.0),
        avg_cpu_limit_m_per_pod=float(np.nanmean(cpu_lim_pp) if cpu_lim_pp else 0.0),
        avg_mem_request_mib_per_pod=float(np.nanmean(mem_req_pp) if mem_req_pp else 0.0),
        avg_mem_limit_mib_per_pod=float(np.nanmean(mem_lim_pp) if mem_lim_pp else 0.0),
        fit_warnings=warnings,
        pods_vary=pods_vary,
    )


def _effective_intercept(intercept: float) -> float:
    return intercept if abs(intercept) >= INTERCEPT_EPS else 0.0


def _resource_demand(r: float, intercept: float, slope: float) -> float:
    if not np.isfinite(slope) or slope <= 0:
        return 0.0
    return max(0.0, _effective_intercept(intercept) + slope * r)


def _resource_demands(r: float, params: DerivedParams) -> Tuple[float, float, float]:
    return (
        _resource_demand(r, params.D_cpu_intercept, params.D_cpu_s),
        _resource_demand(r, params.D_ram_intercept, params.D_ram_s),
        _resource_demand(r, params.D_io_intercept, params.D_io_s),
    )


def _instances_for_demand(demand: float, u_max: float) -> int:
    if u_max <= 0:
        return 1
    if demand <= 0:
        return 1
    return max(1, int(math.ceil(demand / u_max)))


def _per_pod_util(demand: float, instances: int) -> float:
    return demand / max(instances, 1)


def _diagnostic_channels(instances: int, mmc_c: int) -> int:
    return max(instances, mmc_c, 1)


def _finite_or_none(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _scale_max(avg_ms: Optional[float], k_peak: float) -> Optional[float]:
    if avg_ms is None:
        return None
    return _finite_or_none(avg_ms * k_peak)


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


def _diagnostic_latencies(
    r: float,
    params: DerivedParams,
    k_peak: float,
    modeling,
    instances_at_r: int,
) -> Tuple[
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
    float,
    bool,
]:
    c_diag = _diagnostic_channels(instances_at_r, params.mmc_c)
    per_pod_rps = r / c_diag
    unstable = False
    wait_prob = 0.0

    m_m_1_avg = None
    m_m_1_max = None
    if modeling.use_m_m_1:
        raw = mm1_response_time_ms(params.S_ms, per_pod_rps)
        m_m_1_avg = _finite_or_none(raw)
        if raw is not None and not math.isfinite(raw):
            unstable = True
        m_m_1_max = _scale_max(m_m_1_avg, k_peak)

    m_m_c_avg = None
    m_m_c_max = None
    if modeling.use_m_m_c:
        raw, wait_prob = mmc_response_time_ms(params.S_ms, r, c_diag)
        m_m_c_avg = _finite_or_none(raw)
        if raw is not None and not math.isfinite(raw):
            unstable = True
        m_m_c_max = _scale_max(m_m_c_avg, k_peak)

    kingman_avg = None
    kingman_max = None
    if modeling.use_kingman:
        raw = _kingman_R_ms(per_pod_rps, params.S_ms, params.Ca, params.Cs)
        kingman_avg = _finite_or_none(raw)
        if not math.isfinite(raw):
            unstable = True
        kingman_max = _scale_max(kingman_avg, k_peak)

    ggc_avg_ms = None
    ggc_max_ms = None
    if modeling.use_g_g_c:
        raw = _ggc_response_time_ms(r, params.S_ms, params.Ca, params.Cs, c_diag)
        ggc_avg_ms = _finite_or_none(raw)
        if not math.isfinite(raw):
            unstable = True
        ggc_max_ms = _scale_max(ggc_avg_ms, k_peak)

    return (
        m_m_1_avg,
        m_m_1_max,
        m_m_c_avg,
        m_m_c_max,
        kingman_avg,
        kingman_max,
        ggc_avg_ms,
        ggc_max_ms,
        wait_prob,
        unstable,
    )


def _scaling_at_r(
    r: float,
    params: DerivedParams,
    u_max_cpu: float,
    u_max_ram: float,
    u_max_io: float,
) -> Tuple[float, float, float, int, int, int, int, float, float, float]:
    cpu_demand, ram_demand, io_demand = _resource_demands(r, params)
    inst_cpu = _instances_for_demand(cpu_demand, u_max_cpu)
    inst_ram = _instances_for_demand(ram_demand, u_max_ram)
    inst_io = _instances_for_demand(io_demand, u_max_io)
    instances_at_r = max(inst_cpu, inst_ram, inst_io, 1)
    cpu_util = _per_pod_util(cpu_demand, inst_cpu)
    ram_util = _per_pod_util(ram_demand, inst_ram)
    io_util = _per_pod_util(io_demand, inst_io)
    return (
        cpu_demand,
        ram_demand,
        io_demand,
        inst_cpu,
        inst_ram,
        inst_io,
        instances_at_r,
        cpu_util,
        ram_util,
        io_util,
    )


def compute_forecast(data: InputSchema) -> ForecastOutput:
    usable_steps = _filter_usable_steps(data.steps)
    if len(usable_steps) < 2:
        raise ValueError("Фильтр steps: недостаточно данных (исключены все с errors_pct >5%)")

    params = _derive_params(data, usable_steps)
    X_target = float(data.target.target_rps)
    slo_ms = float(data.target.slo_ms_max_optional or 0.0)
    k_peak = params.baseline_ratio_max_to_avg
    modeling = data.modeling

    steps_rps = np.array([float(s.rps) for s in usable_steps], dtype=float)
    steps_avg_ms = np.array([float(s.avg_ms) for s in usable_steps], dtype=float)
    steps_max_ms = np.array([float(s.max_ms) for s in usable_steps], dtype=float)

    empirical_avg_ms, empirical_max_ms = _predict_empirical_pair(
        steps_rps, steps_avg_ms, steps_max_ms, X_target
    )

    cap: Capacity = data.capacity
    u_max_cpu = float(cap.u_max_cpu)
    u_max_ram = float(cap.u_max_ram)
    u_max_io = float(cap.u_max_io_optional or 1.0)

    (
        u_cpu,
        u_ram,
        u_io,
        inst_cpu,
        inst_ram,
        inst_io,
        suggested_m,
        _cpu_util_at_scale,
        _ram_util_at_scale,
        _io_util_at_scale,
    ) = _scaling_at_r(X_target, params, u_max_cpu, u_max_ram, u_max_io)

    (
        m_m_1_avg,
        m_m_1_max,
        m_m_c_avg,
        m_m_c_max,
        kingman_avg,
        kingman_max,
        ggc_avg_ms,
        ggc_max_ms,
        wait_prob,
        unstable_target,
    ) = _diagnostic_latencies(X_target, params, k_peak, modeling, suggested_m)

    cpu_after = u_cpu / suggested_m
    ram_after = u_ram / suggested_m
    io_after = u_io / suggested_m

    targets = TargetsOut(
        rps=X_target,
        latency_ms=TargetsLatency(
            empirical=LatencyPair(avg=empirical_avg_ms, max=empirical_max_ms),
            m_m_1=LatencyPair(avg=m_m_1_avg, max=m_m_1_max)
            if m_m_1_avg is not None and m_m_1_max is not None
            else None,
            m_m_c=LatencyPair(avg=m_m_c_avg, max=m_m_c_max)
            if m_m_c_avg is not None and m_m_c_max is not None
            else None,
            kingman=LatencyPair(avg=kingman_avg, max=kingman_max)
            if kingman_avg is not None and kingman_max is not None
            else None,
            g_g_c=LatencyPair(avg=ggc_avg_ms, max=ggc_max_ms)
            if ggc_avg_ms is not None and ggc_max_ms is not None
            else None,
        ),
        utilization=TargetsUtil(
            cpu=u_cpu,
            ram=u_ram,
            io=u_io,
            cpu_after_replicas=cpu_after,
            ram_after_replicas=ram_after,
            io_after_replicas=io_after,
        ),
        instances=TargetsInstances(
            cpu_based=inst_cpu,
            ram_based=inst_ram,
            io_based=inst_io,
            suggested_m=suggested_m,
        ),
    )

    models = ModelsOut(
        service_time_ms=float(params.S_ms),
        variability=Variability(Ca=params.Ca, Cs=params.Cs),
        mmc=MMCOut(c=int(params.mmc_c), wait_prob=float(wait_prob)),
        kube={
            "cpu_request_m_per_pod": params.avg_cpu_request_m_per_pod,
            "cpu_limit_m_per_pod": params.avg_cpu_limit_m_per_pod,
            "mem_request_mib_per_pod": params.avg_mem_request_mib_per_pod,
            "mem_limit_mib_per_pod": params.avg_mem_limit_mib_per_pod,
        },
        variability_note=VARIABILITY_NOTE,
    )

    min_rps_obs = min(s.rps for s in usable_steps)
    max_rps_obs = max(s.rps for s in usable_steps)
    r_start = float(min_rps_obs)
    r_end = float(max(2.0 * X_target, max_rps_obs * 1.2))
    base_grid = np.linspace(r_start, r_end, num=50).tolist()
    observed_rps = sorted({float(s.rps) for s in usable_steps})
    grid = sorted({float(round(x, 6)) for x in (base_grid + observed_rps)})
    observed_by_rps = _aggregate_observed_by_rps(usable_steps)

    series_latency: List[SeriesLatencyPoint] = []
    series_util: List[SeriesUtilPoint] = []
    series_instances: List[SeriesInstancesPoint] = []
    unstable_series = unstable_target

    for r in grid:
        empirical_avg_r, empirical_max_r = _predict_empirical_pair(
            steps_rps, steps_avg_ms, steps_max_ms, r
        )
        (
            cpu_demand_r,
            ram_demand_r,
            io_demand_r,
            inst_cpu_r,
            inst_ram_r,
            inst_io_r,
            instances_r,
            cpu_util_r,
            ram_util_r,
            io_util_r,
        ) = _scaling_at_r(r, params, u_max_cpu, u_max_ram, u_max_io)

        (
            mm1_avg,
            mm1_max,
            mmc_avg,
            mmc_max,
            k_avg,
            k_max,
            ggc_avg_r,
            ggc_max_r,
            _,
            unstable_r,
        ) = _diagnostic_latencies(r, params, k_peak, modeling, instances_r)
        unstable_series = unstable_series or unstable_r

        observed = observed_by_rps.get(float(round(r, 6)))
        obs_avg = observed[0] if observed else None
        obs_max = observed[1] if observed else None

        series_latency.append(
            SeriesLatencyPoint(
                rps=float(r),
                observed_avg_ms=obs_avg,
                observed_max_ms=obs_max,
                empirical_avg_ms=empirical_avg_r,
                empirical_max_ms=empirical_max_r,
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
        series_util.append(
            SeriesUtilPoint(
                rps=float(r),
                cpu_demand=cpu_demand_r,
                ram_demand=ram_demand_r,
                io_demand=io_demand_r,
                cpu=cpu_util_r,
                ram=ram_util_r,
                io=io_util_r,
            )
        )
        series_instances.append(
            SeriesInstancesPoint(
                rps=float(r),
                instances_cpu=inst_cpu_r,
                instances_ram=inst_ram_r,
                instances_io=inst_io_r,
            )
        )

    series = SeriesOut(
        latency_vs_rps=series_latency, util_vs_rps=series_util, instances_vs_rps=series_instances
    )

    num_steps = len(data.steps)
    steps_df = _build_steps_dataframe(usable_steps)
    linear_steps = _linear_steps_label(steps_df)

    slo_text = f"SLO={int(slo_ms)} мс" if slo_ms > 0 else ""
    avg_range_text = (
        f"{empirical_avg_ms:.1f} мс" if math.isfinite(empirical_avg_ms) else "н/д"
    )
    max_latency_text = (
        f"{empirical_max_ms:.1f} мс" if math.isfinite(empirical_max_ms) else "н/д"
    )

    cpu_pressure = cpu_after / max(1e-9, u_max_cpu)
    ram_pressure = ram_after / max(1e-9, u_max_ram)
    if ram_pressure >= cpu_pressure:
        bottleneck = "RAM"
        bottleneck_pressure = ram_pressure
    else:
        bottleneck = "CPU"
        bottleneck_pressure = cpu_pressure

    primary_model = "Empirical observed curve"
    primary_avg_ms = empirical_avg_ms if math.isfinite(empirical_avg_ms) else None
    primary_max_ms = empirical_max_ms if math.isfinite(empirical_max_ms) else None

    slo_margin_ms = None
    if slo_ms > 0.0 and primary_max_ms is not None:
        slo_margin_ms = slo_ms - primary_max_ms
        slo_status = "ok" if slo_margin_ms >= 0 else "risk"
    elif slo_ms > 0.0:
        slo_status = "risk"
    else:
        slo_status = "unknown"

    max_observed_rps = float(max_rps_obs)
    target_over_observed_ratio = X_target / max(1e-9, max_observed_rps)
    excluded_steps = num_steps - len(usable_steps)
    quality_warnings: List[str] = list(params.fit_warnings)
    if len(usable_steps) < 3:
        quality_warnings.append("Мало пригодных ступеней: прогноз чувствителен к шуму.")
    if excluded_steps > 0:
        quality_warnings.append(f"Исключено ступеней с errors_pct > 5%: {excluded_steps}.")
    if target_over_observed_ratio > 1.5:
        quality_warnings.append("Целевой RPS сильно выше наблюдаемого диапазона: экстраполяция рискованна.")
    if params.pods_vary:
        quality_warnings.append("Число pod'ов менялось между ступенями: спрос усредняется по тесту.")
    if params.Ca > 1.0 or params.Cs > 1.5:
        quality_warnings.append(
            "Высокая вариативность нагрузки или сервиса: queueing-модели используйте только как диагностику."
        )
    quality_warnings.append(VARIABILITY_NOTE)
    if unstable_series:
        quality_warnings.append("На target queueing-модели нестабильны (λ·S близко к 1 или выше).")
    critical_present = any(
        keyword in warning
        for warning in quality_warnings
        for keyword in ("Мало пригодных", "Исключено", "сильно выше", "менялось", "нестабильна", "Не удалось", "нестабильны")
    )
    if not critical_present:
        quality_warnings.insert(0, "Критичных предупреждений по входным данным нет.")

    cpu_headroom_pct = (u_max_cpu - cpu_after) * 100.0
    ram_headroom_pct = (u_max_ram - ram_after) * 100.0

    summary = (
        f"Анализ на основе {num_steps} ступеней: Линейный участок — steps {linear_steps}. "
        f"Сервисное время S={params.S_ms:.1f} мс (low-load avg). На target_rps={X_target:.0f}: "
        f"Empirical avg {avg_range_text}; empirical max {max_latency_text} "
        f"({slo_text}). Основной latency-прогноз построен по наблюдаемой кривой. "
        f"Узкое место: {bottleneck} — масштабируйте на {targets.instances.suggested_m} инстанса."
    )

    return ForecastOutput(
        targets=targets,
        models=models,
        series=series,
        meta=ForecastMeta(
            summary=summary,
            slo_ms_max_optional=str(int(slo_ms)) if slo_ms > 0 else "",
            u_max_ram=f"{u_max_ram:.2f}",
            u_max_cpu=f"{u_max_cpu:.2f}",
            observed_steps=num_steps,
            used_steps=len(usable_steps),
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
            has_unstable_models=unstable_series,
        ),
    )
