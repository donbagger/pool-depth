import io
import json
import os
import unittest
from unittest import mock

import requests

from pool_depth import check_depth
from pool_depth.api import PoolDepthError, make_client
from pool_depth.cli import EXIT_API, EXIT_NO_ESTIMATE, EXIT_OK, EXIT_TOO_THIN, EXIT_USAGE, amount, main

from .helpers import CURVE, ORCA, UNIV2, FakeApi, fake_client, fixture, response


def run(argv, api=None, **client_kw):
    client, api = fake_client(api, **client_kw)
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err, client=client)
    return code, out.getvalue(), err.getvalue(), api


class Cli(unittest.TestCase):
    def test_deep_enough_exits_0(self):
        code, out, _, api = run(["ethereum", UNIV2, "10k"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("USDC/WETH on Uniswap V2 (ethereum)", out)
        self.assertIn("Verdict: deep enough", out)
        self.assertEqual(api.requests[0].path_url, f"/networks/ethereum/pools/{UNIV2}")

    def test_too_thin_exits_1(self):
        code, out, _, _ = run(["ethereum", UNIV2, "1m"])
        self.assertEqual(code, EXIT_TOO_THIN)
        self.assertIn("TOO THIN", out)

    def test_json_output_shape(self):
        code, out, _, _ = run(["solana", ORCA, "50000", "--max-impact", "2", "--json"])
        r = json.loads(out)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(r["pair"], "SOL/USDC")
        self.assertEqual(r["max_impact_pct"], 2.0)
        self.assertEqual(
            sorted(r["directions"][0]),
            ["impact_pct", "max_trade_usd", "pay", "pay_token", "receive", "receive_reserve_usd"],
        )
        self.assertEqual(r["method"], "constant_product_estimate")
        self.assertEqual(r["url"], f"https://dexpaprika.com/solana/pool/{ORCA}")
        for link in ("docs_url", "console_url", "pricing_url"):
            self.assertTrue(r[link].startswith("https://"))

    def test_three_token_pool_exits_4(self):
        code, _, err, _ = run(["ethereum", CURVE, "1000"])
        self.assertEqual(code, EXIT_NO_ESTIMATE)
        self.assertIn("two-token pools", err)

    def test_bad_arguments_exit_2_and_send_nothing(self):
        for argv in (
            ["solana", ORCA],
            ["solana", ORCA, "-5"],
            ["solana", ORCA, "0"],
            ["solana", ORCA, "lots"],
            ["solana", ORCA, "10", "--max-impact", "0"],
            ["solana", ORCA, "10", "--max-impact", "100"],
            ["Sol ana", ORCA, "10"],
            ["solana", "../../admin", "10"],
        ):
            code, _, _, api = run(argv + ["--json"])
            self.assertEqual(code, EXIT_USAGE, argv)
            self.assertEqual(api.requests, [], argv)  # exit 2 promises nothing was sent

    def test_pay_token_not_in_the_pool_exits_4_after_one_request(self):
        code, _, err, api = run(["solana", ORCA, "10", "--pay", "DOGE", "--json"])
        self.assertEqual(code, EXIT_NO_ESTIMATE)
        self.assertEqual(len(api.requests), 1)
        e = json.loads(err)
        self.assertEqual(e["error"], "token_not_in_pool")
        self.assertIn("SOL and USDC", e["message"])

    def test_usage_error_is_json_with_json(self):
        code, out, err, _ = run(["solana", "--json"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err)["error"], "usage")

    def test_api_error_keeps_the_api_code_recorded_404s(self):
        code, _, err, _ = run(["solana", "notapool11111111111111111111111", "10", "--json"])
        self.assertEqual(code, EXIT_API)
        e = json.loads(err)
        self.assertEqual((e["error"], e["status"], e["message"]), ("not_found", 404, "pool not found"))
        self.assertEqual(e["pricing_url"], "https://dexpaprika.com/api/pricing")
        code, _, err, _ = run(["nochain", "abcdefgh", "10", "--json"])
        self.assertEqual(json.loads(err)["error"], "unsupported_network")

    def test_amount_forms(self):
        self.assertEqual(amount("50,000"), 50_000)
        self.assertEqual(amount("$1.5m"), 1_500_000)
        self.assertEqual(amount("2B"), 2e9)


class Transport(unittest.TestCase):
    def test_key_is_the_whole_authorization_value(self):
        _, _, _, api = run(["ethereum", UNIV2, "10"], api_key="  my-key  ")
        self.assertEqual(api.requests[0].headers["Authorization"], "my-key")

    def test_no_key_no_header_even_with_one_in_the_environment(self):
        with mock.patch.dict(os.environ, {"DEXPAPRIKA_API_KEY": "from-env"}):
            _, _, _, api = run(["ethereum", UNIV2, "10"], api_key="")
        self.assertNotIn("Authorization", api.requests[0].headers)

    def test_key_from_environment(self):
        with mock.patch.dict(os.environ, {"DEXPAPRIKA_API_KEY": "from-env"}):
            client = make_client(base_url="")
        self.assertEqual(client.api_key, "from-env")

    def test_only_the_two_dexpaprika_hosts(self):
        self.assertEqual(make_client(api_key="", base_url="https://api-pro.dexpaprika.com/").base_url, "https://api-pro.dexpaprika.com")
        for bad in ("http://api.dexpaprika.com", "https://api.dexpaprika.com.evil.test", "https://example.com"):
            with self.assertRaises(PoolDepthError) as e:
                make_client(api_key="k", base_url=bad)
            self.assertEqual(e.exception.code, "invalid_base_url")

    def test_every_request_has_a_timeout_and_no_redirects(self):
        _, _, _, api = run(["ethereum", UNIV2, "10"])
        self.assertEqual(api.kwargs[0]["timeout"], 5)

        seen = {}

        class Spy(requests.Session):
            def request(self, method, url, **kw):
                seen.update(kw)
                raise requests.ConnectionError("stop")

        from pool_depth.api import _Session

        s = _Session(7)
        with mock.patch.object(requests.Session, "request", Spy.request):
            with self.assertRaises(requests.ConnectionError):
                s.request("GET", "https://api.dexpaprika.com/x")
        self.assertEqual((seen["timeout"], seen["allow_redirects"]), (7, False))

    def test_a_redirect_is_an_error_not_followed(self):
        api = FakeApi(lambda req, n: response(req, 302, b"", {"Location": "https://evil.test/"}))
        code, _, err, api = run(["ethereum", UNIV2, "10", "--json"], api=api, api_key="secret")
        self.assertEqual(code, EXIT_API)
        self.assertEqual(len(api.requests), 1)
        self.assertNotIn("secret", err)

    def test_short_429_is_retried_once(self):
        def handler(req, n):
            if n == 1:
                return response(req, 429, {"error": "rate_limited", "message": "slow down"}, {"Retry-After": "0"})
            return None

        code, _, _, api = run(["ethereum", UNIV2, "10"], api=FakeApi(handler))
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(api.requests), 2)

    def test_429_that_persists_is_reported_with_retry_after(self):
        api = FakeApi(lambda req, n: response(req, 429, {"message": "monthly credits used"}, {"Retry-After": "0"}))
        code, _, err, api = run(["ethereum", UNIV2, "10", "--json"], api=api)
        self.assertEqual(code, EXIT_API)
        self.assertEqual(len(api.requests), 2)  # one try, one retry, then it reports
        e = json.loads(err)
        self.assertEqual((e["error"], e["status"], e["retry_after"]), ("rate_limited", 429, 0.0))
        self.assertIn("console.dexpaprika.com", e["hint"])

    def test_402_payment_required_body_from_the_spec(self):
        body = {"error": "payment_required", "tier": "free", "message": "monthly credits exhausted"}
        api = FakeApi(lambda req, n: response(req, 402, body))
        code, _, err, _ = run(["ethereum", UNIV2, "10", "--json"], api=api)
        e = json.loads(err)
        self.assertEqual((code, e["error"], e["status"], e["tier"]), (EXIT_API, "payment_required", 402, "free"))
        self.assertIn("dexpaprika.com/api/pricing", e["hint"])

    def test_plan_required_passes_required_tier_through(self):
        api = FakeApi(lambda req, n: response(req, 403, fixture("error-plan-required.json")))
        _, _, err, _ = run(["ethereum", UNIV2, "10", "--json"], api=api)
        e = json.loads(err)
        self.assertEqual((e["tier"], e["required_tier"]), ("keyless", "dev"))

    def test_paid_host_html_block_page(self):
        api = FakeApi(lambda req, n: response(req, 403, b"<!DOCTYPE html>blocked", {"Content-Type": "text/html"}))
        code, _, err, _ = run(["ethereum", UNIV2, "10", "--json"], api=api, base_url="https://api-pro.dexpaprika.com")
        self.assertEqual(code, EXIT_API)
        self.assertEqual(json.loads(err)["error"], "blocked")

    def test_recorded_plan_required_body(self):
        api = FakeApi(lambda req, n: response(req, 403, fixture("error-plan-required.json")))
        code, _, err, _ = run(["ethereum", UNIV2, "10", "--json"], api=api)
        self.assertEqual(code, EXIT_API)
        self.assertEqual(json.loads(err)["error"], "plan_required")

    def test_timeout_and_offline(self):
        for exc, code in ((requests.ReadTimeout("slow"), "timeout"), (requests.ConnectionError("down"), "network_error")):
            def handler(req, n, exc=exc):
                raise exc

            rc, _, err, _ = run(["ethereum", UNIV2, "10", "--json"], api=FakeApi(handler))
            self.assertEqual((rc, json.loads(err)["error"]), (EXIT_API, code))


class Library(unittest.TestCase):
    def test_check_depth(self):
        client, _ = fake_client()
        r = check_depth("ethereum", UNIV2, 10_000, client=client)
        self.assertEqual(r["verdict"], "ok")

    def test_check_depth_validates(self):
        client, api = fake_client()
        for kw in ({"trade_usd": 0}, {"trade_usd": 10, "max_impact_pct": 0}):
            with self.assertRaises(PoolDepthError):
                check_depth("ethereum", UNIV2, client=client, **{"trade_usd": 10, **kw})
        self.assertEqual(api.requests, [])


if __name__ == "__main__":
    unittest.main()
