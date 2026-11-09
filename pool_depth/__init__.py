"""pool-depth: is a DEX pool deep enough for the trade you have in mind?

    from pool_depth import check_depth
    result = check_depth("solana", "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE", 50_000)
    result["verdict"]  # "ok" or "too_thin"
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

__version__ = "1.0.0"

from .api import LINKS, PoolDepthError, fetch_pool, make_client  # noqa: E402
from .depth import EstimateError, assess, impact, max_trade  # noqa: E402

__all__ = ["check_depth", "PoolDepthError", "assess", "impact", "max_trade", "make_client", "LINKS", "__version__"]


def check_depth(
    network: str,
    pool: str,
    trade_usd: float,
    max_impact_pct: float = 1.0,
    pay: str | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Fetch one pool from DexPaprika and estimate the price impact of a trade through it.

    Raises PoolDepthError with a stable `code` when the input is bad, the API refuses,
    or the pool's data cannot support an estimate.
    """
    if not (0 < trade_usd <= 1e12):
        raise PoolDepthError("usage", "trade_usd must be above 0 and at most 1e12")
    if not (0 < max_impact_pct < 100):
        raise PoolDepthError("usage", "max_impact_pct must be above 0 and below 100")
    body = fetch_pool(client or make_client(), network, pool)
    try:
        result = assess(body, trade_usd, max_impact_pct, pay)
    except EstimateError as e:
        raise PoolDepthError(e.code, str(e)) from e
    result["url"] = f"https://dexpaprika.com/{quote(result['network'] or network)}/pool/{quote(result['pool'] or pool, safe='')}"
    result.update(LINKS)
    return result
