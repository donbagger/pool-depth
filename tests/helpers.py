"""A fake DexPaprika API for tests, built from responses recorded live on 9 Nov 2026.

It plugs in below the SDK as a requests transport adapter, so every test goes through the
real dexpaprika-sdk code path (headers, retries, error types) without touching the network.
`python smoke.py` is the live check.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Callable

import requests
from requests.adapters import BaseAdapter

from pool_depth.api import _Session, make_client

FIXTURES = Path(__file__).parent / "fixtures"

POOLS = {
    "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE": "solana-orca-sol-usdc.json",
    "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc": "ethereum-univ2-usdc-weth.json",
    "0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7": "ethereum-curve-3pool.json",
}

ORCA = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
UNIV2 = "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc"
CURVE = "0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def response(request, status: int, body, headers: dict | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    r.raw = io.BytesIO(raw)
    r._content = raw
    r.headers.update({"Content-Type": "application/json", **(headers or {})})
    r.url = request.url
    r.request = request
    return r


class FakeApi(BaseAdapter):
    """Answers pool details from fixtures. `handler` can override any request."""

    def __init__(self, handler: Callable | None = None):
        super().__init__()
        self.handler = handler
        self.requests: list[requests.PreparedRequest] = []
        self.kwargs: list[dict] = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        self.kwargs.append(kwargs)
        if self.handler:
            answer = self.handler(request, len(self.requests))
            if answer is not None:
                return answer
        path = request.path_url.split("?")[0]
        if path.startswith("/networks/nochain/"):
            return response(request, 404, fixture("error-unsupported-network.json"))
        name = POOLS.get(path.rsplit("/", 1)[-1])
        if name:
            return response(request, 200, fixture(name))
        return response(request, 404, fixture("error-pool-not-found.json"))

    def close(self):
        pass


def fake_client(api: FakeApi | None = None, api_key: str = "", base_url: str | None = None):
    api = api or FakeApi()
    session = _Session(timeout=5)
    session.mount("https://", api)
    client = make_client(api_key=api_key, base_url=base_url or "", session=session)
    client.backoff_times = [0.0]  # no real sleeping between retries in tests
    return client, api
