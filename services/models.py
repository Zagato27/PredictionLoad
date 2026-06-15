import math
from typing import Tuple


def safe_rho(X: float, S_s: float) -> float:
    return X * S_s


def mm1_response_time_ms(S_ms: float, X: float) -> float:
    """
    M/M/1 average response time in ms.
    S_ms: service time per job (ms)
    X: arrival/throughput in rps
    """
    S_s = S_ms / 1000.0
    rho = safe_rho(X, S_s)
    if rho >= 1.0:
        return float("inf")
    return (S_s / (1.0 - rho)) * 1000.0


def erlang_c_wait_probability(a: float, c: int) -> float:
    """
    Erlang C wait probability for M/M/c.
    a = lambda / mu (traffic offered), c servers.
    """
    if c <= 0 or a < 0:
        return 0.0
    rho = a / c
    # Avoid invalid regimes
    if rho >= 1.0:
        return 1.0
    # Compute P0
    sum_terms = 0.0
    for n in range(c):
        sum_terms += (a**n) / math.factorial(n)
    last_term = (a**c) / (math.factorial(c) * (1.0 - rho))
    denom = sum_terms + last_term
    if denom == 0:
        return 1.0
    P0 = 1.0 / denom
    Pw = last_term * P0
    return max(0.0, min(1.0, Pw))


def mmc_response_time_ms(S_ms: float, X: float, c: int) -> Tuple[float, float]:
    """
    M/M/c average response time in ms and wait probability.
    Returns (R_ms, Pw).
    """
    if c <= 0:
        return float("inf"), 1.0
    mu = 1.0 / (S_ms / 1000.0)
    lam = X
    a = lam / mu
    Pw = erlang_c_wait_probability(a, c)
    # Avoid division by zero
    denom = c * mu - lam
    if denom <= 0:
        return float("inf"), 1.0
    Wq_s = Pw / denom
    R_s = Wq_s + (1.0 / mu)
    return R_s * 1000.0, Pw


