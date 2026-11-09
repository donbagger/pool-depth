# pool-depth

[![ci](https://github.com/donbagger/pool-depth/actions/workflows/ci.yml/badge.svg)](https://github.com/donbagger/pool-depth/actions/workflows/ci.yml)
[![Powered by DexPaprika](https://dexpaprika.com/badges/powered-by-dexpaprika.svg?t=bwt_qwzN90m0N8yYGeH4)](https://dexpaprika.com)

Is this pool deep enough for my trade? Give it a pool and a dollar amount and it
estimates the price impact both ways, tells you the largest trade that stays under your
limit, and answers with an exit code a script or an agent can act on.

```
$ pool-depth solana Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE 50k
SOL/USDC on Orca (solana), liquidity $24.18M, updated 2026-11-09T10:06:45Z

Trade of $50,000, limit 1% price impact:
  pay SOL for USDC  impact   1.62%   largest trade under 1%: $30,695
  pay USDC for SOL  impact   0.24%   largest trade under 1%: $213,441

Verdict: TOO THIN (worst 1.62% against your 1%)

Constant-product estimate from the reserve of the token you receive, before fees. Concentrated-liquidity and stable-swap pools can fill better or worse. Get a DEX quote before trading.
https://dexpaprika.com/solana/pool/Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE
```

Built on the official [DexPaprika Python SDK](https://pypi.org/project/dexpaprika-sdk/)
and the pool data in the [DexPaprika API](https://docs.dexpaprika.com). No API key
needed to start.

## Run it

Python 3.10 or newer.

```bash
git clone https://github.com/donbagger/pool-depth
cd pool-depth
python -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install --no-deps .

pool-depth ethereum 0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc 25k
pool-depth solana Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE 50k --pay USDC
pool-depth base <pool-address> 1.5m --max-impact 0.5 --json
```

`python -m pool_depth ...` works too, without the second install step.

| Argument | |
|---|---|
| `network` | a network id: `solana`, `ethereum`, `base`, ... ([all of them](https://api.dexpaprika.com/networks)) |
| `pool` | the pool address on that network |
| `amount` | trade size in USD: `50000`, `50,000`, `50k`, `1.5m` |
| `--max-impact PCT` | your limit, default `1` (percent) |
| `--pay TOKEN` | judge only the direction that pays in this token, by symbol or address |
| `--json` | JSON on stdout, and errors as JSON on stderr |
| `--timeout SEC` | per request, default `15` |
| `-h, --help` / `-V, --version` | |

Don't have a pool address? Find one on [dexpaprika.com](https://dexpaprika.com), or list
the busiest pools on a network with
[pool-pulse](https://github.com/donbagger/pool-pulse).

## How the estimate works

pool-depth makes one request, `GET /networks/{network}/pools/{address}`, and reads
`token_reserves` from it: how much of each token the pool holds, in USD.

A trade worth `D` dollars against a side worth `R` dollars has a price impact of
`D / (R + D)` in a constant-product pool (`x * y = k`, the Uniswap V2 design).
The largest trade under a limit `p` is then `p * R / (1 - p)`.

`R` is the reserve of the token you receive. In a concentrated-liquidity pool that token
sits in the price ranges your trade moves through, so it is the side that runs out. In a
constant-product pool both sides are worth the same, and the result matches the exact
`x * y = k` math to within the gap between the two sides' USD values (on the recorded
Uniswap V2 pool in the tests, 9.68% against an exact 9.70%).

Treat it as a screen, before fees:

- **Concentrated liquidity** (Uniswap V3, Orca Whirlpools, Raydium CLMM) can fill better
  or worse than this near the current price, depending on where liquidity sits. Its two
  sides are often far apart in value, as in the Orca example above, which is why the two
  directions differ.
- **Stable-swap pools** between pegged assets usually fill much better.
- **Pools with three or more tokens** get exit code 4 instead of a guess.
- **Fees** are not included.

Before a real trade, get a quote from the DEX or an aggregator. This is not financial
advice.

## API key

You don't need one. Without a key DexPaprika limits requests per IP, and each run makes
one request (two if the first one fails and is retried). A free key from [console.dexpaprika.com](https://console.dexpaprika.com)
raises the limit; current limits are on the
[pricing page](https://dexpaprika.com/api/pricing).

```bash
export DEXPAPRIKA_API_KEY=your-key
```

The SDK sends it in the `Authorization` header on its own, with nothing in front of it.
On a paid plan, also set `DEXPAPRIKA_BASE_URL=https://api-pro.dexpaprika.com`.
pool-depth refuses any other base URL and does not follow redirects, so the key only ever
goes to DexPaprika.

## For scripts and agents

The exit code is the answer:

| Exit code | Meaning |
|---|---|
| `0` | deep enough |
| `1` | too thin for your limit |
| `2` | bad arguments or `DEXPAPRIKA_BASE_URL`; nothing was sent |
| `3` | the API refused or the network failed; `error` in the JSON says which |
| `4` | the pool was fetched but cannot answer this: three or more tokens, no reserves, or a `--pay` token it does not hold |

```
$ pool-depth solana Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE 50k --pay DOGE
pool-depth: DOGE is not in this pool. It holds SOL and USDC.
Pass one of the pool's two tokens to --pay, by symbol or address.
$ echo $?
4
```

```bash
pool-depth ethereum 0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc 25k --json && echo "go ahead"
```

`--json` gives you everything in the table and more:

```json
{
  "network": "ethereum",
  "pool": "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",
  "pair": "USDC/WETH",
  "dex": "Uniswap V2",
  "liquidity_usd": 18648602.349429607,
  "last_updated": "2026-11-09T09:49:15Z",
  "stale": false,
  "trade_usd": 1000000.0,
  "max_impact_pct": 1.0,
  "directions": [
    {
      "pay": "USDC",
      "pay_token": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
      "receive": "WETH",
      "receive_reserve_usd": 9335547.29435372,
      "impact_pct": 9.6753,
      "max_trade_usd": 94298.46
    },
    {
      "pay": "WETH",
      "pay_token": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
      "receive": "USDC",
      "receive_reserve_usd": 9312345.629125996,
      "impact_pct": 9.6971,
      "max_trade_usd": 94064.1
    }
  ],
  "worst_impact_pct": 9.6971,
  "verdict": "too_thin",
  "method": "constant_product_estimate",
  "note": "Constant-product estimate from the reserve of the token you receive, before fees. Concentrated-liquidity and stable-swap pools can fill better or worse. Get a DEX quote before trading.",
  "url": "https://dexpaprika.com/ethereum/pool/0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",
  "docs_url": "https://docs.dexpaprika.com",
  "console_url": "https://console.dexpaprika.com",
  "pricing_url": "https://dexpaprika.com/api/pricing"
}
```

Errors keep the API's own error code (`not_found`, `unsupported_network`,
`rate_limited`, `payment_required`, ...) plus a `hint`, `retry_after` in seconds for rate
limits, and the API's `tier`, `required_tier` and `param` when its error body has them.

As a Python function, for an agent framework's tool list:

```python
from pool_depth import check_depth

result = check_depth("solana", "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE", 50_000, max_impact_pct=1.0)
print(result["verdict"], result["worst_impact_pct"])   # for example: too_thin 1.51
```

It raises `PoolDepthError`, whose `code` is the same string the CLI prints. Coding agents
working on this repo should read [AGENTS.md](AGENTS.md) first.

## Tests

```bash
python -m unittest -v   # offline: the real SDK against responses recorded from the live API
python smoke.py         # live, 3 requests
```

CI runs the offline tests on Python 3.10 to 3.13 and the live check daily, so a green
badge means it worked against the API today.

## Links

- [DexPaprika docs](https://docs.dexpaprika.com) and the
  [pool details reference](https://docs.dexpaprika.com/api-reference/pools/get-a-pool-on-a-network)
- [Free API key](https://console.dexpaprika.com)
- [Plans and limits](https://dexpaprika.com/api/pricing)
- [pool-pulse](https://github.com/donbagger/pool-pulse): the most traded pools on a network
- [dexpaprika-agent-kit](https://github.com/donbagger/dexpaprika-agent-kit): small Python agents on the same data

MIT licensed.
