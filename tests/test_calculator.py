from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext
import pytest
from pydantic import ValidationError
from trade_pricing.models import *
from trade_pricing.core.calculator import calculate
from trade_pricing.core.money import price_for_margin, money
from .helpers import *


def run(case=None, costs=None, fx=None, policy=None, price="15", now=NOW):
    a,b,c,d = base_models()
    return calculate(CaseInput.model_validate(case) if case else a,
                     CostBook.model_validate(costs) if costs else b,
                     FxBook.model_validate(fx) if fx else c,
                     Policy.model_validate(policy) if policy else d, price, now)


def test_exact_contribution_and_fees():
    r = run()
    assert r["status"] == "calculated"
    t = r["totals"]
    assert t["known_order_cost"] == "1150.00"
    assert t["revenue"] == "1500.00" and t["revenue_fee"] == "30.00"
    assert t["contribution"] == "320.00" and t["contribution_margin"] == "0.21333333"


def test_auto_price_reaches_target_after_rounding():
    r = run(price=None)
    assert Decimal(r["totals"]["contribution_margin"]) >= Decimal("0.2")


@pytest.mark.parametrize("value", [1.1, 1, "NaN", "Infinity", "-1", "1e9", "01", "1.123456789", "9"*16])
def test_unsafe_amounts_are_rejected(value):
    data = costs_data()
    data["lines"][0]["amount"] = value
    with pytest.raises(ValidationError):
        CostBook.model_validate(data)


def test_missing_cost_is_unknown_not_zero():
    c = costs_data(); c["lines"] = [x for x in c["lines"] if x["category"] != "packaging"]
    r = run(costs=c)
    assert "MISSING_COST_CATEGORY:packaging" in r["blockers"] and r["totals"] is None


def test_explicit_confirmed_zero_charge_is_supported():
    c = costs_data(); c["lines"][1]["amount"] = "0"
    assert run(costs=c)["status"] == "calculated"


def test_buyer_paid_charge_excluded():
    c = costs_data(); c["lines"].append(line("buyer_freight", "main_carriage", "9999", payer="buyer"))
    r = run(costs=c)
    assert r["totals"]["known_order_cost"] == "1150.00" and len(r["excluded_costs"]) == 1


def test_bundle_double_count_is_blocked():
    c = costs_data(); c["lines"][0]["covers"] = ["packaging"]
    assert any("DUPLICATE_BUNDLED_COST" in x for x in run(costs=c)["blockers"])


def test_bundle_replaces_separate_charge():
    c = costs_data(); c["lines"][0]["covers"] = ["packaging"]; del c["lines"][1]
    assert run(costs=c)["status"] == "calculated"


def test_multiple_additive_product_components_are_allowed():
    c = costs_data(); c["lines"].append(line("assembly", "product", "1", "per_unit"))
    assert run(costs=c)["status"] == "calculated"


@pytest.mark.parametrize("field,value", [("product_revision","v2"),("unit","kilogram"),("payment_terms","NET60"),("named_place","Another terminal")])
def test_cost_scope_mismatch(field,value):
    c = case_data(); c[field] = value
    assert f"COST_SCOPE_MISMATCH:{field}" in run(case=c)["blockers"]


def test_unit_mismatch_does_not_auto_convert():
    c = costs_data(); c["lines"][0]["unit"] = "kilogram"
    assert "UNIT_MISMATCH:product" in run(costs=c)["blockers"]


def test_quantity_outside_supplier_range():
    c = case_data(); c["quantity"] = "201"
    assert "QUANTITY_OUTSIDE_CONFIRMED_COST_RANGE" in run(case=c)["blockers"]


def test_lead_time_violation():
    c=case_data(); c["max_lead_days"]=10
    assert "LEAD_TIME_EXCEEDS_REQUIREMENT" in run(case=c)["blockers"]


@pytest.mark.parametrize("offset,code", [(31,"EXPIRED_EVIDENCE"),(-2,"FUTURE_EVIDENCE")])
def test_expired_or_future_evidence(offset,code):
    r=run(now=NOW+timedelta(days=offset))
    assert any(x.startswith(code) for x in r["blockers"])


def test_missing_exchange_rate():
    c=costs_data(); c["lines"][0]["currency"]="CNY"
    assert "MISSING_FX:CNY" in run(costs=c)["blockers"]


def test_explicit_fx_direction_and_precision():
    c=costs_data(); c["lines"][0].update(currency="CNY", amount="70")
    fx={"target_currency":"USD","rates":[{"source_currency":"CNY","rate":"0.142857142857","evidence":evidence("fx")}]}
    assert run(costs=c,fx=fx)["totals"]["known_order_cost"]=="1150.00"


def test_expired_fx_blocks():
    c=costs_data(); c["lines"][0]["currency"]="CNY"
    e=evidence(); e["observed_at"]=(NOW-timedelta(days=3)).isoformat(); e["valid_until"]=(NOW-timedelta(days=1)).isoformat()
    fx={"target_currency":"USD","rates":[{"source_currency":"CNY","rate":"0.14","evidence":e}]}
    assert "EXPIRED_EVIDENCE:FX:CNY" in run(costs=c,fx=fx)["blockers"]


def test_margin_not_markup():
    r=run(price="13.8")
    # 20% markup over cost does not produce 20% contribution margin.
    assert Decimal(r["totals"]["contribution_margin"]) < Decimal("0.2")


def test_below_floor_visible_but_not_approvable():
    r=run(price="10")
    assert r["status"]=="blocked" and "MARGIN_BELOW_POLICY_FLOOR" in r["blockers"]
    assert Decimal(r["totals"]["contribution"])<0


def test_price_precision_rejected_instead_of_silently_rounded():
    r=run(price="15.00001")
    assert r["blockers"]==["UNIT_PRICE_SCALE_EXCEEDS_POLICY"]


def test_cif_requires_freight_and_insurance():
    ca,co=case_data(),costs_data()
    for d in (ca,co): d.update(incoterm="CIF",transport="sea",named_place="Test destination port")
    for category in ["origin_loading","origin_transport","export_clearance","export_handling"]:
        co["lines"].append(line(category,category,"0"))
    r=run(case=ca,costs=co)
    assert set(r["missing_categories"])=={"main_carriage","insurance"}


def test_maritime_term_cannot_be_air():
    ca,co=case_data(),costs_data()
    for d in(ca,co): d.update(incoterm="FOB",transport="air")
    assert "MARITIME_TERM_REQUIRES_MARITIME_MODE" in run(case=ca,costs=co)["blockers"]


def test_ddp_never_pretends_import_compliance_is_solved():
    ca,co=case_data(),costs_data()
    for d in(ca,co): d["incoterm"]="DDP"
    assert "DDP_FORMAL_ISSUANCE_NOT_SUPPORTED_V1" in run(case=ca,costs=co)["blockers"]


def test_fca_variant_required():
    ca=case_data();ca["incoterm"]="FCA"
    with pytest.raises(ValidationError):CaseInput.model_validate(ca)


def test_date_without_timezone_rejected():
    ca=case_data();ca["valid_until"]="2026-12-01T00:00:00"
    with pytest.raises(ValidationError):CaseInput.model_validate(ca)


def test_impossible_margin_rejected():
    p=policy_data();p.update(target_margin="0.99",revenue_fee_rate="0.02")
    with pytest.raises(ValidationError):Policy.model_validate(p)


def test_expiry_is_earliest_applicable_source():
    co=costs_data();co["lines"][0]["evidence"]["valid_until"]=(NOW+timedelta(days=2)).isoformat()
    assert datetime.fromisoformat(run(costs=co)["valid_until"])==NOW+timedelta(days=2)


def test_reproducible_with_explicit_clock():
    assert run()==run()


@pytest.mark.parametrize("currency",["USD","JPY","KWD"])
def test_rounded_floor_property_across_currency_and_quantities(currency):
    # Deterministic 180-case numerical sweep per currency, not a market simulation.
    with localcontext() as ctx:
        ctx.prec=64
        for q in [Decimal("0.000001"),Decimal("0.3"),Decimal(1),Decimal(7),Decimal(100),Decimal(10000)]:
            for rawcost in ["0.01","1.17","99.99","1150","78293.19"]:
                for margin in [Decimal(0),Decimal("0.15"),Decimal("0.3"),Decimal("0.77"),Decimal("0.9"),Decimal("0.97")]:
                    cost=money(Decimal(rawcost),currency)
                    if cost<=0:continue
                    fee=Decimal("0.02")
                    p=price_for_margin(cost,q,fee,margin,currency,4)
                    revenue=money(p*q,currency)
                    charged=money(revenue*fee,currency)
                    assert (revenue-cost-charged)/revenue>=margin


def test_fractional_pieces_rejected():
    c=case_data();c.update(quantity='1.5',unit='piece')
    with pytest.raises(ValidationError):CaseInput.model_validate(c)
