# Security

Please report a vulnerability privately through
[GitHub's private reporting](https://github.com/donbagger/pool-depth/security/advisories/new)
rather than in a public issue. I aim to answer within a few days.

## What pool-depth does with your key

- It reads `DEXPAPRIKA_API_KEY` from the environment and hands it to the official
  `dexpaprika-sdk`, which sends it only in the `Authorization` header.
- Requests go only to `https://api.dexpaprika.com` or `https://api-pro.dexpaprika.com`.
  Any other `DEXPAPRIKA_BASE_URL` is refused before a request is made, and redirects are
  not followed.
- Every request has a timeout, and a rate limit is retried once at most.
- The key is never printed, logged or stored. `.env` is in `.gitignore`.

## Untrusted data

Token symbols come from on-chain metadata that anyone can set. pool-depth strips control
characters and bidirectional overrides from every string it gets from the API, in the text
output and in the JSON.

## Supply chain

One runtime dependency, `dexpaprika-sdk`, installed from a requirements file pinned with
hashes. CI actions are pinned to commit SHAs, Dependabot keeps both current, and the
workflow token is read-only.

For problems in the DexPaprika API itself, see https://docs.dexpaprika.com.
