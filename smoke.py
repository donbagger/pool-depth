"""Live check against the real API, run daily in CI. Three requests.

Exits non-zero, naming what broke, if the API stops answering the way pool-depth expects:
a renamed field, a missing reserve, a changed error code.

    python smoke.py
"""

import io
import json
import sys

from pool_depth.cli import EXIT_API, EXIT_OK, EXIT_TOO_THIN, main

failures = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main([*argv, "--json"], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


# Orca SOL/USDC on Solana and Uniswap V2 USDC/WETH on Ethereum: large, long-lived pools.
for network, pool in (
    ("solana", "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"),
    ("ethereum", "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc"),
):
    code, out, err = run(network, pool, "10000")
    check(code in (EXIT_OK, EXIT_TOO_THIN), f"{network} pool answers (exit {code}) {err.strip()}")
    if out:
        r = json.loads(out)
        reserves = [d["receive_reserve_usd"] for d in r["directions"]]
        check("/" in r["pair"] and all(x > 0 for x in reserves), f"{r['pair']} has two positive reserves {reserves}")
        check(0 < r["worst_impact_pct"] < 100, f"impact {r['worst_impact_pct']}% is a percentage")
        check(not r["stale"], f"reserves updated recently ({r['last_updated']})")

code, _, err = run("not-a-chain", "abcdefgh", "100")
check(code == EXIT_API and json.loads(err)["error"] == "unsupported_network", f"unknown network reported as unsupported_network (exit {code})")

sys.exit(1 if failures else 0)
