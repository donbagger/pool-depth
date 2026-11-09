"""Talking to DexPaprika through the official Python SDK (dexpaprika-sdk on PyPI).

The SDK already sends the key the right way (the whole Authorization value, nothing in
front of it), honours Retry-After on a 429, and raises typed errors that carry the API's
own error code. This module adds the three things a CLI needs on top: a timeout on every
request, a short retry budget so a rate limit is reported instead of waited out for
minutes, and a base URL that can only ever be one of DexPaprika's two API hosts.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import requests
from dexpaprika_sdk import DexPaprikaClient
from dexpaprika_sdk.exceptions import ApiHTTPError, DexPaprikaError, KeyRefusedError

from .depth import clean

FREE_HOST = "https://api.dexpaprika.com"
PAID_HOST = "https://api-pro.dexpaprika.com"
ALLOWED_HOSTS = (FREE_HOST, PAID_HOST)

LINKS = {
    "docs_url": "https://docs.dexpaprika.com",
    "console_url": "https://console.dexpaprika.com",
    "pricing_url": "https://dexpaprika.com/api/pricing",
}

NETWORK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# EVM (0x + 40 hex), Solana (base58), Sui and Aptos (0x + 64 hex), TON (base64url) and others.
POOL_ADDRESS = re.compile(r"^[A-Za-z0-9_:.\-]{8,128}$")

# One per error code in the API's OpenAPI spec (ErrorBody and QuotaErrorBody), plus our own.
HINTS = {
    "not_found": "Check the pool address and that it is on this network.",
    "payment_required": "The credits for this period are used up. Plans and limits: dexpaprika.com/api/pricing.",
    "invalid_api_key": "DEXPAPRIKA_API_KEY was refused. Copy it again from console.dexpaprika.com, the key on its own.",
    "forbidden": "This key cannot make this request. Check your plan at console.dexpaprika.com.",
    "preview_only": "Your plan gets a preview of this data. See dexpaprika.com/api/pricing.",
    "invalid_param": "Check the arguments against pool-depth --help.",
    "missing_param": "Check the arguments against pool-depth --help.",
    "route_not_found": "This version of pool-depth calls an endpoint the API does not have. Update pool-depth.",
    "endpoint_removed": "This version of pool-depth calls a removed endpoint. Update pool-depth.",
    "internal_error": "The API had a problem. Try again in a minute.",
    "unavailable": "The API is briefly unavailable. Try again in a minute.",
    "unsupported_pool": "pool-depth estimates two-token pools only.",
    "token_not_in_pool": "Pass one of the pool's two tokens to --pay, by symbol or address.",
    "unsupported_network": "Network ids look like solana, ethereum, base. Full list: GET /networks.",
    "rate_limited": "Wait and try again, or add a free key from console.dexpaprika.com for a higher limit.",
    "unauthorized": "DEXPAPRIKA_API_KEY was refused. Copy it again from console.dexpaprika.com, the key on its own.",
    "blocked": "The paid host only accepts a paid key. Set DEXPAPRIKA_API_KEY, or unset DEXPAPRIKA_BASE_URL.",
    "plan_required": "This needs a paid plan. See dexpaprika.com/api/pricing.",
    "wrong_host": "Free keys use https://api.dexpaprika.com. Unset DEXPAPRIKA_BASE_URL.",
    "timeout": "The API did not answer in time. Try again, or raise --timeout.",
    "network_error": "Could not reach the API. Check your connection.",
}


class PoolDepthError(Exception):
    """Anything that stops a result: bad input, an API refusal, or no connection.

    `code` is stable and meant for programs; for API refusals it is the API's own
    `error` field (not_found, rate_limited, plan_required, ...).
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int | None = None,
        retry_after: float | None = None,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after
        self.hint = HINTS.get(code) or (
            f"See {LINKS['docs_url']} for this error." if status is not None else None
        )
        # From the API's error body when it has them: the plan the request was judged
        # against, the lowest plan that would allow it, and the parameter at fault.
        extra = extra or {}
        self.tier = extra.get("tier")
        self.required_tier = extra.get("required_tier")
        self.param = extra.get("param")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": str(self),
            "status": self.status,
            "retry_after": self.retry_after,
            "hint": self.hint,
            "tier": self.tier,
            "required_tier": self.required_tier,
            "param": self.param,
            **LINKS,
        }


class _Session(requests.Session):
    """A requests session with a default timeout and no redirects.

    requests has no session-wide timeout, so without this a stalled connection can hang the
    CLI indefinitely. Redirects are refused so the key cannot be carried to another host.
    """

    def __init__(self, timeout: float):
        super().__init__()
        self._timeout = timeout

    def request(self, method, url, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self._timeout)
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


def resolve_base_url(raw: str | None) -> str:
    value = (raw or FREE_HOST).strip().rstrip("/")
    if value not in ALLOWED_HOSTS:
        raise PoolDepthError(
            "invalid_base_url",
            f"DEXPAPRIKA_BASE_URL must be {FREE_HOST} or {PAID_HOST}, got {value!r}",
        )
    return value


def make_client(
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 15.0,
    session: requests.Session | None = None,
) -> DexPaprikaClient:
    """A DexPaprikaClient with the key and host taken from the environment unless given."""
    from . import __version__

    key = (api_key if api_key is not None else os.environ.get("DEXPAPRIKA_API_KEY", "")).strip()
    return DexPaprikaClient(
        base_url=resolve_base_url(base_url if base_url is not None else os.environ.get("DEXPAPRIKA_BASE_URL")),
        session=session or _Session(timeout),
        user_agent=f"pool-depth/{__version__}",
        # Always a string: "" means keyless here, where None would let the SDK read the
        # environment a second time behind our back.
        api_key=key,
        # One retry. The SDK honours Retry-After (up to 60 s) on each one, so the default of
        # four could hold a terminal for minutes on a rate limit.
        max_retries=1,
    )


def fetch_pool(client: DexPaprikaClient, network: str, address: str) -> dict[str, Any]:
    """GET /networks/{network}/pools/{address}, as the raw JSON body.

    The raw body, through client.get, because the SDK's PoolDetails model (0.14.2) leaves
    out token_reserves and liquidity_usd, which are exactly what this tool needs.
    """
    if not NETWORK_ID.match(network):
        raise PoolDepthError("usage", f"Not a network id: {network!r}")
    if not POOL_ADDRESS.match(address):
        raise PoolDepthError("usage", f"Not a pool address: {address!r}")
    try:
        body = client.get(f"/networks/{quote(network)}/pools/{quote(address, safe='')}")
    except KeyRefusedError as e:
        raise PoolDepthError("blocked", "The paid host refused the request (HTTP 403).", 403) from e
    except ApiHTTPError as e:
        raise _from_http(e) from e
    except requests.HTTPError as e:
        raise _from_http(e) from e
    except requests.Timeout as e:
        raise PoolDepthError("timeout", "No answer from the API in time.") from e
    except requests.ConnectionError as e:
        raise PoolDepthError("network_error", f"Could not reach the API: {e.__class__.__name__}") from e
    except DexPaprikaError as e:
        raise PoolDepthError("api_error", str(e)) from e
    except ValueError as e:  # a 3xx or a body that is not JSON
        raise PoolDepthError("bad_response", "The API answered with something other than JSON.") from e
    if not isinstance(body, dict) or "tokens" not in body:
        raise PoolDepthError("bad_response", "The API answered without pool data.")
    return body


def _from_http(e: requests.HTTPError) -> PoolDepthError:
    response = e.response
    status = getattr(response, "status_code", None)
    body: dict[str, Any] = {}
    try:
        parsed = response.json() if response is not None else {}
        body = parsed if isinstance(parsed, dict) else {}
    except ValueError:
        pass
    code = getattr(e, "error_code", None) or body.get("error") or ("rate_limited" if status == 429 else f"http_{status}")
    message = clean(body["message"])[:300] if isinstance(body.get("message"), str) else f"HTTP {status} from the API"
    retry_after = None
    if status in (402, 429) and response is not None:
        try:
            retry_after = float(response.headers.get("Retry-After") or body.get("retry_after"))
        except (TypeError, ValueError):
            retry_after = None
    extra = {k: clean(body[k])[:60] for k in ("tier", "required_tier", "param") if isinstance(body.get(k), str)}
    return PoolDepthError(clean(code)[:60], message, status, retry_after, extra)
