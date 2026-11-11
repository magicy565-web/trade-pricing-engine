from collections import defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
from ..models import CashPlan, FxBook
from ..errors import DomainError
from .calculator import evidence_issues
from .money import decimal_string as ds, money


def evaluate_cashflow(plan: CashPlan, fx: FxBook, now: datetime) -> dict:
    issues = evidence_issues(plan.evidence, now, "cash_plan")
    with localcontext() as ctx:
        ctx.prec = 64
        daily = defaultdict(lambda: Decimal(0))
        rates = {x.source_currency: x for x in fx.rates}
        for event in plan.events:
            if event.day > plan.horizon_days:
                issues.append("EVENT_OUTSIDE_HORIZON")
            value = Decimal(event.amount)
            if event.currency != fx.target_currency:
                item = rates.get(event.currency)
                if not item:
                    issues.append(f"MISSING_FX:{event.currency}")
                    continue
                issues.extend(evidence_issues(item.evidence, now, "cash_fx"))
                value *= Decimal(item.rate)
            daily[event.day] += value * (1 if event.direction == "inflow" else -1)
        if issues:
            raise DomainError("CASHFLOW_EVIDENCE", "; ".join(sorted(set(issues))))
        balance = peak = exposure_days = Decimal(0)
        previous = 0
        timeline = []
        for day in sorted(daily):
            exposure_days += max(-balance, Decimal(0)) * (day - previous)
            balance += daily[day]
            peak = max(peak, -balance)
            timeline.append({"day": day, "net_flow": ds(daily[day]), "balance": ds(balance)})
            previous = day
        exposure_days += max(-balance, Decimal(0)) * (plan.horizon_days - previous)
        funding = exposure_days * Decimal(plan.annual_rate) / 365
        return {"currency": fx.target_currency, "peak_funding": ds(money(peak, fx.target_currency)),
                "financing_cost_estimate": ds(money(funding, fx.target_currency)),
                "ending_balance": ds(money(balance, fx.target_currency)), "timeline": timeline,
                "basis": "declared schedule; same-day netting; simple interest ACT/365; zero opening cash",
                "included_in_quote": False,
                "warnings": ["NO_TAX_REBATE_OR_PAYMENT_TIMING_INFERRED", "INTRADAY_FUNDING_NOT_MODELLED"]}
