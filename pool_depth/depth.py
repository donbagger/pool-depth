"""The estimate itself. Pure functions over a pool-details response, no network.

In a constant-product pool (x * y = k), a trade worth D dollars against a side worth R
dollars fills at an average price D / (R + D) worse than the price before the trade. That
fraction is the price impact this module reports. Turned around, the largest trade that
stays under a limit p is D = p * R / (1 - p).

In x * y = k the exact formula uses the reserve of the token you pay. Both sides of such a
pool are worth the same at the pool's own price, so the token you receive gives the same
number up to the gap between DexPaprika's USD values for the two sides (0.25% on the
recorded Uniswap V2 pool in tests/fixtures, which moves a 9.70% impact to 9.68%).

R is the reserve of the token you receive. In a concentrated-liquidity pool (Uniswap V3,
Orca Whirlpools, Raydium CLMM) the two sides can differ several times over (6.5 to 1 on the
recorded Orca SOL/USDC pool), and the token you receive is the one held in the price
ranges your trade moves through, so it is the side that runs out. The reserve of the token
you pay would understate the impact there.

It is a screen, not a quote. Pool fees are not included, concentrated liquidity can fill
better or worse than this near the current price, and stable-swap pools usually fill much
better. Before a real trade, get a quote from the DEX or an aggregator.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# C0 and C1 control characters and bidirectional overrides. Token symbols are set by
# whoever deployed the token, so they are cleaned before anyone prints them.
_UNSAFE = re.compile("[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]")

# Reserves older than this are flagged, because the estimate is only as fresh as they are.
STALE_AFTER_SECONDS = 6 * 60 * 60

METHOD = "constant_product_estimate"
NOTE = (
    "Constant-product estimate from the reserve of the token you receive, before fees. "
    "Concentrated-liquidity and stable-swap pools can fill better or worse. "
    "Get a DEX quote before trading."
)


class EstimateError(ValueError):
    """The pool's data cannot support an estimate (wrong shape, no reserves)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def clean(value: Any) -> str:
    return _UNSAFE.sub("", "" if value is None else str(value))


def impact(trade_usd: float, reserve_usd: float) -> float:
    """Price impact as a fraction (0.01 is 1%) of a trade_usd trade against a reserve_usd side."""
    if reserve_usd <= 0:
        raise EstimateError("no_reserves", "The pool reports no reserve for the token you would receive.")
    return trade_usd / (reserve_usd + trade_usd)


def max_trade(max_impact: float, reserve_usd: float) -> float:
    """The largest trade in USD whose impact stays at or under max_impact (a fraction below 1)."""
    if not 0 < max_impact < 1:
        raise ValueError("max_impact must be between 0 and 1")
    return max_impact * reserve_usd / (1 - max_impact)


def assess(
    pool: dict[str, Any],
    trade_usd: float,
    max_impact_pct: float = 1.0,
    pay: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Estimate both directions of a trade through a two-token pool and give a verdict.

    `pool` is the JSON body of GET /networks/{network}/pools/{address}. `pay` narrows the
    verdict to one direction: a token symbol (case-insensitive) or address.
    """
    tokens = {t["id"]: t for t in pool.get("tokens") or []}
    reserves = [r for r in pool.get("token_reserves") or [] if r.get("token_id") in tokens]
    if len(tokens) != 2 or len(reserves) != 2:
        raise EstimateError(
            "unsupported_pool",
            f"pool-depth handles two-token pools; this one has {len(tokens)} tokens "
            f"and {len(reserves)} reserves.",
        )

    def symbol(token_id: str) -> str:
        return clean(tokens[token_id].get("symbol") or token_id[:8])

    limit = max_impact_pct / 100
    directions = []
    for given, received in (reserves, reserves[::-1]):
        reserve_out = float(received.get("reserve_usd") or 0)
        directions.append({
            "pay": symbol(given["token_id"]),
            "pay_token": clean(given["token_id"]),
            "receive": symbol(received["token_id"]),
            "receive_reserve_usd": reserve_out,
            "impact_pct": round(impact(trade_usd, reserve_out) * 100, 4),
            "max_trade_usd": round(max_trade(limit, reserve_out), 2),
        })

    counted = directions
    if pay:
        wanted = pay.strip().lower()
        counted = [d for d in directions if wanted in (d["pay"].lower(), d["pay_token"].lower())]
        if not counted:
            raise EstimateError(
                "token_not_in_pool",
                f"{clean(pay)} is not in this pool. It holds {directions[0]['pay']} and {directions[1]['pay']}.",
            )

    worst = max(d["impact_pct"] for d in counted)
    age = _age_seconds(pool.get("last_updated"), now or datetime.now(timezone.utc))
    return {
        "network": clean(pool.get("chain")),
        "pool": clean(pool.get("id")),
        "pair": "/".join(symbol(r["token_id"]) for r in reserves),
        "dex": clean(pool.get("dex_name") or pool.get("dex_id")),
        "liquidity_usd": pool.get("liquidity_usd"),
        "last_updated": clean(pool.get("last_updated")) or None,
        "stale": age is not None and age > STALE_AFTER_SECONDS,
        "trade_usd": trade_usd,
        "max_impact_pct": max_impact_pct,
        "directions": directions,
        "worst_impact_pct": worst,
        "verdict": "ok" if worst <= max_impact_pct else "too_thin",
        "method": METHOD,
        "note": NOTE,
    }


def _age_seconds(stamp: Any, now: datetime) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds()
