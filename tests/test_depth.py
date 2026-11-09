import unittest
from datetime import datetime, timezone

from pool_depth.depth import EstimateError, assess, clean, impact, max_trade

from .helpers import fixture

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)


def pool(reserve_a: float, reserve_b: float, updated: str = "2026-11-09T09:50:00Z") -> dict:
    return {
        "id": "p1",
        "chain": "testnet",
        "dex_name": "Test DEX",
        "liquidity_usd": reserve_a + reserve_b,
        "last_updated": updated,
        "tokens": [{"id": "a", "symbol": "AAA"}, {"id": "b", "symbol": "BBB"}],
        "token_reserves": [
            {"token_id": "a", "reserve": 1, "reserve_usd": reserve_a},
            {"token_id": "b", "reserve": 1, "reserve_usd": reserve_b},
        ],
    }


class Math(unittest.TestCase):
    def test_impact_is_trade_over_reserve_plus_trade(self):
        self.assertAlmostEqual(impact(10_000, 990_000), 0.01)
        self.assertAlmostEqual(impact(1_000_000, 1_000_000), 0.5)

    def test_max_trade_inverts_impact(self):
        for limit in (0.001, 0.01, 0.05, 0.3):
            for reserve in (1e3, 2.5e6, 7e9):
                self.assertAlmostEqual(impact(max_trade(limit, reserve), reserve), limit)

    def test_matches_constant_product_swap_math(self):
        # x * y = k with both sides worth $1 per unit: pay dx of X, get y * dx / (x + dx) of Y.
        x = y = 1_000_000.0
        dx = 25_000.0
        out = y * dx / (x + dx)
        self.assertAlmostEqual(1 - out / dx, impact(dx, y))

    def test_empty_reserve_cannot_be_estimated(self):
        with self.assertRaises(EstimateError) as e:
            impact(100, 0)
        self.assertEqual(e.exception.code, "no_reserves")


class Assess(unittest.TestCase):
    def test_uses_the_reserve_of_the_token_received(self):
        r = assess(pool(1_000_000, 10_000_000), 100_000, 1.0, now=NOW)
        pay_a, pay_b = r["directions"]
        self.assertEqual((pay_a["pay"], pay_a["receive"]), ("AAA", "BBB"))
        self.assertAlmostEqual(pay_a["impact_pct"], round(100_000 / 10_100_000 * 100, 4))
        self.assertAlmostEqual(pay_b["impact_pct"], round(100_000 / 1_100_000 * 100, 4))

    def test_verdict_takes_the_worst_direction(self):
        self.assertEqual(assess(pool(1e6, 1e6), 5_000, 1.0, now=NOW)["verdict"], "ok")
        self.assertEqual(assess(pool(1e6, 1e6), 50_000, 1.0, now=NOW)["verdict"], "too_thin")
        # deep on one side only: buying BBB is fine, selling it is not
        self.assertEqual(assess(pool(1e5, 1e8), 5_000, 1.0, now=NOW)["verdict"], "too_thin")

    def test_pay_narrows_the_verdict_to_one_direction(self):
        p = pool(1e5, 1e8)
        self.assertEqual(assess(p, 5_000, 1.0, pay="aaa", now=NOW)["verdict"], "ok")
        self.assertEqual(assess(p, 5_000, 1.0, pay="BBB", now=NOW)["verdict"], "too_thin")
        self.assertEqual(assess(p, 5_000, 1.0, pay="b", now=NOW)["verdict"], "too_thin")  # by address

    def test_pay_with_a_token_not_in_the_pool(self):
        with self.assertRaises(EstimateError) as e:
            assess(pool(1e6, 1e6), 1_000, pay="ZZZ", now=NOW)
        self.assertEqual(e.exception.code, "token_not_in_pool")

    def test_exactly_at_the_limit_is_ok(self):
        trade = max_trade(0.01, 1e6)
        self.assertEqual(assess(pool(1e6, 1e6), trade, 1.0, now=NOW)["verdict"], "ok")

    def test_close_to_exact_swap_math_on_a_recorded_v2_pool(self):
        # Simulate x * y = k on the raw token amounts and compare with the estimate.
        p = fixture("ethereum-univ2-usdc-weth.json")
        r = assess(p, 1_000_000, 1.0, now=NOW)
        reserves = {x["token_id"]: x for x in p["token_reserves"]}
        for d in r["directions"]:
            paid = reserves[d["pay_token"]]
            got = next(x for k, x in reserves.items() if k != d["pay_token"])
            x, y = paid["reserve"], got["reserve"]
            dx = 1_000_000 / (paid["reserve_usd"] / x)
            exact = 1 - (y * dx / (x + dx)) / dx / (y / x)
            self.assertAlmostEqual(d["impact_pct"], exact * 100, delta=exact * 100 * 0.005)

    def test_three_token_pool_is_refused_recorded_curve(self):
        with self.assertRaises(EstimateError) as e:
            assess(fixture("ethereum-curve-3pool.json"), 1_000)
        self.assertEqual(e.exception.code, "unsupported_pool")

    def test_stale_reserves_are_flagged(self):
        self.assertFalse(assess(pool(1e6, 1e6), 1, now=NOW)["stale"])
        self.assertTrue(assess(pool(1e6, 1e6, "2026-11-08T09:00:00Z"), 1, now=NOW)["stale"])

    def test_recorded_uniswap_v2_pool(self):
        r = assess(fixture("ethereum-univ2-usdc-weth.json"), 1_000_000, 1.0, now=NOW)
        self.assertEqual(r["pair"], "USDC/WETH")
        self.assertEqual(r["dex"], "Uniswap V2")
        # both sides of a v2 pool hold about the same value, so both directions agree
        a, b = (d["impact_pct"] for d in r["directions"])
        self.assertAlmostEqual(a, b, delta=0.1)
        self.assertEqual(r["verdict"], "too_thin")

    def test_hostile_symbols_are_cleaned(self):
        p = pool(1e6, 1e6)
        p["tokens"][0]["symbol"] = "A\u001b[2J\u009b31m\u202eA"
        r = assess(p, 1, now=NOW)
        self.assertEqual(r["directions"][0]["pay"], "A[2J31mA")
        self.assertEqual(clean("x\u0007\u2066y"), "xy")


if __name__ == "__main__":
    unittest.main()
