from ashare_agent.execution_rules import assess_tradeability, infer_price_limits


def quote(**overrides):
    payload = {
        "last_price": 10.0,
        "prev_price": 10.0,
        "volume": 1_000_000,
        "price_change_ratio_pct": 0.0,
        "listing_days": 100,
        "board": "mainboard",
    }
    payload.update(overrides)
    return payload


def test_supplier_limits_are_preferred_and_limit_locks_do_not_fill():
    payload = quote(upper_limit_price=10.5, lower_limit_price=9.5)
    assert infer_price_limits(payload) == (10.5, 9.5, "supplier")
    assert assess_tradeability({**payload, "last_price": 10.5}, "BUY").state == "BUY_BLOCKED_LIMIT_UP"
    assert assess_tradeability({**payload, "last_price": 9.5}, "SELL").state == "EXIT_BLOCKED_LIMIT_DOWN"


def test_st_and_ordinary_limits_are_derived_only_with_known_listing_age():
    ordinary = infer_price_limits(quote())
    special = infer_price_limits(quote(is_st=True))
    unknown = infer_price_limits(quote(listing_days=None))
    assert ordinary[:2] == (11.0, 9.0)
    assert special[:2] == (10.5, 9.5)
    assert unknown == (None, None, "unavailable")


def test_suspension_and_missing_limit_information_fail_closed():
    assert assess_tradeability(quote(suspended=True), "BUY").state == "SUSPENDED"
    missing = quote(listing_days=None, price_change_ratio_pct=None)
    assert assess_tradeability(missing, "SELL").state == "LIMIT_DATA_MISSING"

