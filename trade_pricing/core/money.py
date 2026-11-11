from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, localcontext
from typing import Any
import hashlib
import json

CURRENCY_DP = {"USD": 2, "CNY": 2, "EUR": 2, "GBP": 2, "JPY": 0, "CAD": 2, "AUD": 2, "AED": 2, "KWD": 3}


def quantum(dp: int) -> Decimal:
    return Decimal(1).scaleb(-dp)


def money(value: Decimal, currency: str) -> Decimal:
    return value.quantize(quantum(CURRENCY_DP[currency]), rounding=ROUND_HALF_UP)


def decimal_string(value: Decimal) -> str:
    return format(value, "f")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def price_for_margin(cost: Decimal, quantity: Decimal, fee_rate: Decimal,
                     margin: Decimal, currency: str, price_dp: int) -> Decimal:
    """Conservatively rounded floor; verifies invoice/fee rounding, not just algebra."""
    with localcontext() as ctx:
        ctx.prec = 64
        step = quantum(price_dp)
        price = (cost / (1 - margin - fee_rate) / quantity).quantize(step, rounding=ROUND_CEILING)
        bump = (quantum(CURRENCY_DP[currency]) / quantity).quantize(step, rounding=ROUND_CEILING)
        for _ in range(8):
            revenue = money(price * quantity, currency)
            fee = money(revenue * fee_rate, currency)
            if revenue > 0 and (revenue - cost - fee) / revenue >= margin:
                return price
            price += max(step, bump)
        raise ArithmeticError("could not establish a conservative rounded price")
