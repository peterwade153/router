from typing import Optional


def build_route_optimization_cache_key(
    orig_state: str,
    orig_city: Optional[str],
    orig_county: Optional[str],
    dest_state: str,
    dest_city: Optional[str],
    dest_county: Optional[str],
    version: str = "v1",
) -> str:
    """Generates a clean, deterministic cache key based on resolved administrative inputs."""
    o_st = orig_state.strip().upper()
    o_ci = orig_city.strip().lower().replace(" ", "_") if orig_city else "centroid"
    o_co = orig_county.strip().lower().replace(" ", "_") if orig_county else "none"

    d_st = dest_state.strip().upper()
    d_ci = dest_city.strip().lower().replace(" ", "_") if dest_city else "centroid"
    d_co = dest_county.strip().lower().replace(" ", "_") if dest_county else "none"

    return f"route_opt:{version}:route:orig:{o_st}:{o_co}:{o_ci}:dest:{d_st}:{d_co}:{d_ci}"
