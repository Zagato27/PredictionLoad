from typing import Dict, List, Optional, Union

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, conint, confloat, validator, model_validator
    V2 = True
except Exception:
    # Pydantic v1 fallback
    from pydantic import BaseModel, Field, conint, confloat, validator  # type: ignore
    V2 = False


positive_float = confloat(gt=0)  # type: ignore
non_negative_float = confloat(ge=0)  # type: ignore
util_float = confloat(ge=0, le=1)  # type: ignore


class Step(BaseModel):
    # step can be either a human-readable label or an ordinal number.
    step: Union[str, conint(ge=1)]  # type: ignore
    rps: positive_float  # type: ignore
    avg_ms: positive_float  # type: ignore
    max_ms: confloat(ge=0)  # type: ignore
    errors_pct: non_negative_float  # type: ignore
    # Legacy utilization ratios, kept for API compatibility.
    cpu_util: Optional[util_float] = None  # type: ignore
    ram_util: Optional[util_float] = None  # type: ignore
    io_util: Optional[util_float] = None  # type: ignore
    # Kubernetes metrics.
    pods: Optional[conint(ge=1)] = None  # type: ignore
    cpu_usage_m: Optional[non_negative_float] = None  # type: ignore
    cpu_request_m_per_pod: Optional[non_negative_float] = None  # type: ignore
    cpu_limit_m_per_pod: Optional[non_negative_float] = None  # type: ignore
    mem_workingset_mib: Optional[non_negative_float] = None  # type: ignore
    mem_request_mib_per_pod: Optional[non_negative_float] = None  # type: ignore
    mem_limit_mib_per_pod: Optional[non_negative_float] = None  # type: ignore
    concurrency_optional: Optional[conint(ge=0)] = None  # type: ignore

    @validator("max_ms")
    def check_max_ge_avg(cls, v, values):  # type: ignore
        avg = values.get("avg_ms", None)
        if avg is not None and v < avg:
            raise ValueError("max_ms < avg_ms")
        return v


class Target(BaseModel):
    target_rps: positive_float  # type: ignore
    slo_ms_max_optional: Optional[positive_float] = None  # type: ignore


class Capacity(BaseModel):
    u_max_cpu: util_float  # type: ignore
    u_max_ram: util_float  # type: ignore
    u_max_io_optional: Optional[util_float] = None  # type: ignore
    mmc_c_optional: Optional[conint(ge=1)] = None  # type: ignore


class ModelingFlags(BaseModel):
    use_m_m_1: bool = True
    use_m_m_c: bool = True
    use_kingman: bool = True
    use_g_g_c: bool = True


class InputSchema(BaseModel):
    steps: List[Step]
    target: Target
    capacity: Capacity
    modeling: ModelingFlags

    if V2:
        @model_validator(mode="after")  # type: ignore
        def check_steps(self):
            if len(self.steps) < 2:
                raise ValueError("Недостаточно steps для регрессии — минимум 2")
            return self
    else:
        @validator("steps")
        def at_least_two_steps(cls, v):  # type: ignore
            if len(v) < 2:
                raise ValueError("Недостаточно steps для регрессии — минимум 2")
            return v


# Output schemas

class LatencyPair(BaseModel):
    avg: float
    max: float


class TargetsLatency(BaseModel):
    m_m_1: Optional[LatencyPair] = None
    m_m_c: Optional[LatencyPair] = None
    kingman: Optional[LatencyPair] = None
    g_g_c: Optional[LatencyPair] = None


class TargetsUtil(BaseModel):
    cpu: float
    ram: float
    io: float


class TargetsInstances(BaseModel):
    cpu_based: int
    ram_based: int
    suggested_m: int


class TargetsOut(BaseModel):
    rps: float
    latency_ms: TargetsLatency
    utilization: TargetsUtil
    instances: TargetsInstances


class Variability(BaseModel):
    Ca: float
    Cs: float


class MMCOut(BaseModel):
    c: int
    wait_prob: float


class ModelsOut(BaseModel):
    service_time_ms: float
    variability: Variability
    mmc: MMCOut
    kube: Optional[Dict[str, float]] = None  # avg requests/limits per pod, etc.


class SeriesLatencyPoint(BaseModel):
    rps: float
    observed_avg_ms: Optional[float] = None
    observed_max_ms: Optional[float] = None
    m_m_1_avg_ms: Optional[float] = None
    m_m_1_max_ms: Optional[float] = None
    m_m_c_avg_ms: Optional[float] = None
    m_m_c_max_ms: Optional[float] = None
    kingman_avg_ms: Optional[float] = None
    kingman_max_ms: Optional[float] = None
    g_g_c_avg_ms: Optional[float] = None
    g_g_c_max_ms: Optional[float] = None


class SeriesUtilPoint(BaseModel):
    rps: float
    cpu: Optional[float] = None
    ram: Optional[float] = None
    io: Optional[float] = None


class SeriesInstancesPoint(BaseModel):
    rps: float
    instances_cpu: Optional[int] = None
    instances_ram: Optional[int] = None
    instances_io: Optional[int] = None


class SeriesOut(BaseModel):
    latency_vs_rps: List[SeriesLatencyPoint]
    util_vs_rps: List[SeriesUtilPoint]
    instances_vs_rps: List[SeriesInstancesPoint]


class ForecastMeta(BaseModel):
    summary: str
    slo_ms_max_optional: str = ""
    u_max_ram: str
    u_max_cpu: str
    observed_steps: int
    used_steps: int
    excluded_steps: int
    linear_steps: str
    max_observed_rps: float
    target_over_observed_ratio: float
    bottleneck: str
    bottleneck_pressure: float
    recommended_replicas: int
    primary_model: str
    primary_avg_ms: Optional[float] = None
    primary_max_ms: Optional[float] = None
    slo_status: str
    slo_margin_ms: Optional[float] = None
    cpu_headroom_pct: float
    ram_headroom_pct: float
    quality_warnings: List[str] = Field(default_factory=list)


class ForecastOutput(BaseModel):
    targets: TargetsOut
    models: ModelsOut
    series: SeriesOut
    meta: ForecastMeta


