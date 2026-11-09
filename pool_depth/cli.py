"""Command line: pool-depth NETWORK POOL AMOUNT_USD [options].

main() returns the exit code instead of exiting, so tests can call it in-process.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, TextIO

from . import __version__, check_depth
from .api import LINKS, PoolDepthError, make_client

EXIT_OK = 0  # deep enough
EXIT_TOO_THIN = 1  # the estimate is over your limit
EXIT_USAGE = 2  # bad arguments; nothing was sent
EXIT_API = 3  # the API refused, or the network failed
EXIT_NO_ESTIMATE = 4  # the pool was fetched but cannot answer what was asked

# Exit 2 promises that nothing was sent, so only errors caught before the request belong
# in it. --pay naming a token the pool does not hold is only known after the fetch: 4.
_NO_ESTIMATE = {"unsupported_pool", "no_reserves", "token_not_in_pool"}
_USAGE = {"usage", "invalid_base_url"}

EPILOG = f"""\
exit codes:
  0  deep enough    1  too thin    2  bad arguments or DEXPAPRIKA_BASE_URL, nothing sent
  3  API or network error
  4  the pool was fetched but cannot answer this: three or more tokens, no
     reserves, or a --pay token the pool does not hold

environment:
  DEXPAPRIKA_API_KEY   optional; sent as the Authorization header, the key on its own
  DEXPAPRIKA_BASE_URL  optional; https://api-pro.dexpaprika.com for paid plans

Docs {LINKS['docs_url']}  Free key {LINKS['console_url']}  Plans {LINKS['pricing_url']}
"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise PoolDepthError("usage", message)


def amount(text: str) -> float:
    """50000, 50,000, 50k, 1.5m or 2b, in USD."""
    m = re.fullmatch(r"\$?\s*([0-9][0-9_,]*(?:\.[0-9]+)?)\s*([kmb]?)", text.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError(f"not an amount: {text!r}")
    value = float(m.group(1).replace(",", "").replace("_", "")) * {"": 1, "k": 1e3, "m": 1e6, "b": 1e9}[m.group(2)]
    if not 0 < value <= 1e12:
        raise argparse.ArgumentTypeError(f"amount must be above 0 and at most 1e12, got {text!r}")
    return value


def percent(text: str) -> float:
    try:
        value = float(text.strip().rstrip("%"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a percentage: {text!r}") from None
    if not 0 < value < 100:
        raise argparse.ArgumentTypeError(f"--max-impact must be above 0 and below 100, got {text!r}")
    return value


def seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {text!r}") from None
    if not 1 <= value <= 120:
        raise argparse.ArgumentTypeError("--timeout must be from 1 to 120 seconds")
    return value


def parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="pool-depth",
        description="Estimate the price impact of a trade through one DEX pool, on DexPaprika data.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("network", help="network id: solana, ethereum, base, ...")
    p.add_argument("pool", help="pool address on that network")
    p.add_argument("amount", type=amount, help="trade size in USD: 50000, 50k, 1.5m")
    p.add_argument("--max-impact", type=percent, default=1.0, metavar="PCT", help="your limit in percent (default 1)")
    p.add_argument("--pay", metavar="TOKEN", help="only judge the direction that pays in this token (symbol or address)")
    p.add_argument("--timeout", type=seconds, default=15.0, metavar="SEC", help="per request (default 15)")
    p.add_argument("--json", action="store_true", help="print JSON, errors included")
    p.add_argument("-V", "--version", action="version", version=f"pool-depth {__version__}")
    return p


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None, client: Any = None) -> int:
    out, err = stdout or sys.stdout, stderr or sys.stderr
    argv = sys.argv[1:] if argv is None else argv
    want_json = "--json" in argv
    try:
        args = parser().parse_args(argv)
        result = check_depth(
            args.network.lower(),
            args.pool,
            args.amount,
            args.max_impact,
            args.pay,
            client=client or make_client(timeout=args.timeout),
        )
    except PoolDepthError as e:
        if want_json:
            err.write(json.dumps(e.to_dict()) + "\n")
        else:
            err.write(f"pool-depth: {e}\n" + (f"{e.hint}\n" if e.hint else ""))
        if e.code in _USAGE:
            return EXIT_USAGE
        return EXIT_NO_ESTIMATE if e.code in _NO_ESTIMATE else EXIT_API

    out.write(json.dumps(result, indent=2) + "\n" if want_json else render(result))
    return EXIT_OK if result["verdict"] == "ok" else EXIT_TOO_THIN


def usd(v: float | None) -> str:
    if v is None:
        return "-"
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= size:
            return f"${v / size:.2f}{suffix}"
    return f"${v:,.2f}"


def render(r: dict[str, Any]) -> str:
    limit = r["max_impact_pct"]
    width = max(len(f"{d['pay']} for {d['receive']}") for d in r["directions"])
    lines = [
        f"{r['pair']} on {r['dex']} ({r['network']}), liquidity {usd(r['liquidity_usd'])}, updated {r['last_updated'] or 'unknown'}"
        + ("  [reserves look stale]" if r["stale"] else ""),
        "",
        f"Trade of ${r['trade_usd']:,.0f}, limit {limit:g}% price impact:",
    ]
    for d in r["directions"]:
        lines.append(
            f"  pay {d['pay'] + ' for ' + d['receive']:<{width}}  impact {d['impact_pct']:6.2f}%   "
            f"largest trade under {limit:g}%: ${d['max_trade_usd']:,.0f}"
        )
    verdict = "deep enough" if r["verdict"] == "ok" else "TOO THIN"
    lines += [
        "",
        f"Verdict: {verdict} (worst {r['worst_impact_pct']:.2f}% against your {limit:g}%)",
        "",
        r["note"],
        r["url"],
        "",
    ]
    return "\n".join(lines)
