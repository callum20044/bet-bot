import math


def kalshi_taker_fee(contracts: int, price: float) -> float:
    """Kalshi's published taker fee: 0.07 x contracts x P x (1-P), rounded UP to the next cent.
    It's largest for 50c contracts and tiny near 0c or 100c."""
    raw = 0.07 * contracts * price * (1 - price)
    return math.ceil(round(raw * 100, 6)) / 100


def fee_per_contract(price: float) -> float:
    # Rough per-contract fee for edge maths (ignores rounding, which matters less for bigger orders).
    return 0.07 * price * (1 - price)
