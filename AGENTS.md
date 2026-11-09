# AGENTS.md

Notes for coding agents (Claude Code, Codex, Cursor and the like) working in this repo, and
for agents that call pool-depth as a tool.

## Calling pool-depth as a tool

- `pool-depth <network> <pool> <usd> --json`, or `python -m pool_depth ...` from a checkout.
- The exit code is the answer: `0` deep enough, `1` too thin, `2` your arguments were
  wrong (nothing was sent; do not retry unchanged), `3` the API or network failed, `4` the
  pool was fetched but cannot answer this (three or more tokens, no reserves, or a `--pay`
  token it does not hold, in which case the message names the two tokens it does hold).
- On success read stdout as JSON: `verdict`, `worst_impact_pct`, and per direction
  `impact_pct` and `max_trade_usd`. On failure read stderr as JSON: `error` is a stable
  code, `hint` says what to do, `retry_after` is seconds when the API gave one.
- Time budget: one request with a `--timeout` (default 15 s). The SDK retries it once
  after a timeout, a connection error, a 5xx or a 429; for a 429 it first waits the API's
  `Retry-After`, capped at 60 s. The worst case is about `2 * timeout + 60 s`. Give your
  loop its own deadline.
- Tell the user it is an estimate. `method` and `note` in the JSON say so; repeat it rather
  than presenting the number as a quote.
- In Python: `from pool_depth import check_depth`; it raises `PoolDepthError` with `.code`.
- Need a pool address first? `GET https://api.dexpaprika.com/networks/{network}/pools/search`
  or the pool-pulse tool. One request per pool-depth call. Without a key the API limits
  requests per IP; a free key from https://console.dexpaprika.com raises the limit, and
  plans are on https://dexpaprika.com/api/pricing.

## Working on the code

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt
python -m unittest -v      # offline, under a second, run it after every change
python smoke.py            # live API, 3 requests, run it before you push
```

- `pool_depth/depth.py`: the math and the verdict, pure functions, no I/O.
  `pool_depth/api.py`: the SDK client, timeout, host allowlist, error mapping.
  `pool_depth/cli.py`: arguments, output, exit codes. `pool_depth/__init__.py`: `check_depth`.
- Tests mount a fake transport under the real `dexpaprika-sdk`, so they exercise the SDK's
  headers, retries and error types. Fixtures in `tests/fixtures/` are unedited live
  responses; re-record them with curl, never hand-write fields.
- One runtime dependency, `dexpaprika-sdk`, pinned with hashes. To change it, edit
  `requirements.in` and regenerate with the command at its top.
- Pool details go through `client.get(...)` on purpose: the SDK's `PoolDetails` model
  (0.14.2) does not carry `token_reserves` or `liquidity_usd`. Check before switching.
- `token_reserves` is not in the public OpenAPI spec yet, but the live response has it
  (see `tests/fixtures/`): a list of `{"token_id", "reserve", "reserve_usd"}`, one per
  token, where `reserve` is in the token's smallest unit and `reserve_usd` is its value.

## Rules that keep it safe

- The API key goes in the `Authorization` header with nothing in front of it. Never log
  it, print it, put it in a URL or write it to a file.
- `resolve_base_url` accepts exactly two hosts, and `_Session` sets a timeout and refuses
  redirects on every request. These keep a key from reaching anyone else and a stalled
  connection from hanging an agent; do not loosen them.
- Every string from the API goes through `clean()` before output. Token symbols are set by
  whoever created the token and can carry terminal escape codes.
- Keep error codes stable; agents branch on them.
- Do not hard-code rate limits or quotas. Link the pricing page.
