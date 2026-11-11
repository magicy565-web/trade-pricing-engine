from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from ..models import CaseInput, CostBook, FxBook, Policy, Evidence
from .money import money, decimal_string as ds, price_for_margin, quantum
from .trade_rules import required_categories, RULE_VERSION


def evidence_issues(e: Evidence, now: datetime, label: str) -> list[str]:
    issues = []
    if e.observed_at > now:
        issues.append(f"FUTURE_EVIDENCE:{label}")
    if e.valid_until <= now:
        issues.append(f"EXPIRED_EVIDENCE:{label}")
    return issues


def calculate(case: CaseInput, costs: CostBook, fx: FxBook, policy: Policy,
              proposed: str | None, now: datetime) -> dict:
    """Pure evaluation: explicit clock + immutable input = reproducible result."""
    with localcontext() as ctx:
        ctx.prec = 64
        return _calculate(case, costs, fx, policy, proposed, now)


def _calculate(case, costs, fx, policy, proposed, now):
    issues: list[str] = []
    warnings = ["CONTRIBUTION_MARGIN_NOT_ACCOUNTING_NET_PROFIT", "EVIDENCE_CONFIRMATION_IS_NOT_THIRD_PARTY_VERIFICATION"]
    validities = [case.valid_until, costs.evidence.valid_until, policy.evidence.valid_until]
    issues += evidence_issues(costs.evidence, now, "cost_scope")
    issues += evidence_issues(policy.evidence, now, "policy")
    if case.valid_until <= now:
        issues.append("CASE_EXPIRED")
    fields = ["product_id", "product_revision", "incoterm", "named_place", "transport", "fca_delivery", "payment_terms", "unit"]
    for field in fields:
        if getattr(case, field) != getattr(costs, field):
            issues.append(f"COST_SCOPE_MISMATCH:{field}")
    q = Decimal(case.quantity)
    if not Decimal(costs.quantity_min) <= q <= Decimal(costs.quantity_max):
        issues.append("QUANTITY_OUTSIDE_CONFIRMED_COST_RANGE")
    if case.max_lead_days is not None and costs.lead_time_days > case.max_lead_days:
        issues.append("LEAD_TIME_EXCEEDS_REQUIREMENT")
    if case.incoterm in ("FOB", "CIF") and case.transport not in ("sea", "inland_waterway"):
        issues.append("MARITIME_TERM_REQUIRES_MARITIME_MODE")
    if case.incoterm == "DDP":
        issues.append("DDP_FORMAL_ISSUANCE_NOT_SUPPORTED_V1")
    if case.incoterm in ("FOB", "CIF"):
        warnings.append("CONFIRM_ONBOARD_DELIVERY_NOT_TERMINAL_HANDOVER")
    if fx.target_currency != case.currency:
        issues.append("FX_TARGET_CURRENCY_MISMATCH")
    rates = {x.source_currency: x for x in fx.rates}
    included, excluded, covered = [], [], set()
    bundle_coverage = set()
    primary_categories = {x.category for x in costs.lines if x.payer == "seller"}
    total = Decimal(0)
    for line in costs.lines:
        if line.payer == "buyer":
            excluded.append({"id": line.id, "label": line.label, "reason": "buyer_pays"})
            continue
        validities.append(line.evidence.valid_until)
        issues += evidence_issues(line.evidence, now, line.id)
        if line.basis == "per_unit" and line.unit != case.unit:
            issues.append(f"UNIT_MISMATCH:{line.id}")
        overlaps = set(line.covers) & (primary_categories | bundle_coverage)
        if overlaps:
            issues.append(f"DUPLICATE_BUNDLED_COST:{line.id}:{','.join(sorted(overlaps))}")
        bundle_coverage.update(line.covers)
        covered.update([line.category, *line.covers])
        rate = Decimal(1)
        if line.currency != case.currency:
            rate_item = rates.get(line.currency)
            if rate_item is None:
                issues.append(f"MISSING_FX:{line.currency}")
                continue
            issues += evidence_issues(rate_item.evidence, now, f"FX:{line.currency}")
            validities.append(rate_item.evidence.valid_until)
            rate = Decimal(rate_item.rate)
        amount = Decimal(line.amount) * (q if line.basis == "per_unit" else 1) * rate
        total += amount
        included.append({"id": line.id, "label": line.label, "category": line.category,
                         "source_amount": line.amount, "source_currency": line.currency,
                         "basis": line.basis, "fx_rate": ds(rate), "converted_unrounded": ds(amount),
                         "covers": line.covers, "evidence_reference": line.evidence.reference,
                         "evidence_sha256": line.evidence.sha256})
    missing = sorted(required_categories(case.incoterm, case.fca_delivery) - covered)
    issues += [f"MISSING_COST_CATEGORY:{category}" for category in missing]
    if total <= 0:
        issues.append("NONPOSITIVE_KNOWN_COST")
    validity = min(validities).astimezone(timezone.utc).isoformat()
    result = {
        "engine_version": "0.1.0", "rules_version": RULE_VERSION,
        "calculated_at": now.astimezone(timezone.utc).isoformat(), "valid_until": validity,
        "currency": case.currency, "quantity": case.quantity, "unit": case.unit,
        "cost_lines": included, "excluded_costs": excluded,
        "missing_categories": missing, "blockers": sorted(set(issues)), "warnings": warnings,
        "status": "needs_evidence", "totals": None,
        "rounding": {"currency": "ROUND_HALF_UP", "price_floor": "ROUND_CEILING",
                     "unit_price_decimals": policy.unit_price_decimals,
                     "cost": "sum unrounded converted lines, then round once",
                     "fee_base": "rounded total invoice revenue"},
    }
    if issues:
        return result
    cost = money(total, case.currency)
    fee = Decimal(policy.revenue_fee_rate)
    target = price_for_margin(cost, q, fee, Decimal(policy.target_margin), case.currency, policy.unit_price_decimals)
    floor = price_for_margin(cost, q, fee, Decimal(policy.minimum_margin), case.currency, policy.unit_price_decimals)
    breakeven = price_for_margin(cost, q, fee, Decimal(0), case.currency, policy.unit_price_decimals)
    price = Decimal(proposed) if proposed is not None else target
    if price <= 0:
        result["blockers"] = ["NONPOSITIVE_PRICE"]
        return result
    if price != price.quantize(quantum(policy.unit_price_decimals)):
        result["blockers"] = ["UNIT_PRICE_SCALE_EXCEEDS_POLICY"]
        return result
    revenue = money(price * q, case.currency)
    if revenue <= 0:
        result["blockers"] = ["REVENUE_ROUNDS_TO_ZERO"]
        return result
    revenue_fee = money(revenue * fee, case.currency)
    contribution = revenue - cost - revenue_fee
    margin = contribution / revenue
    if margin < Decimal(policy.minimum_margin):
        result["blockers"].append("MARGIN_BELOW_POLICY_FLOOR")
    result["status"] = "blocked" if result["blockers"] else "calculated"
    result["totals"] = {
        "known_order_cost": ds(cost), "unit_price": ds(price), "revenue": ds(revenue),
        "revenue_fee": ds(revenue_fee), "contribution": ds(contribution),
        "contribution_margin": ds(margin.quantize(Decimal("0.00000001"))),
        "target_unit_price": ds(target), "minimum_policy_unit_price": ds(floor),
        "break_even_unit_price": ds(breakeven),
        "discount_headroom_per_unit": ds(max(Decimal(0), price - floor)),
        "tax_basis": costs.tax_basis,
        "financing": "not inferred; include an approved explicit cost line when applicable",
    }
    return result
