import pytest
from trade_pricing.core.cashflow import evaluate_cashflow
from trade_pricing.errors import DomainError
from .helpers import *


def plan(events):
    return CashPlan(fx_snapshot_id="fx-id",annual_rate="0.365",horizon_days=20,evidence=evidence(),events=events)


def test_cashflow_peak_and_actual_days():
    p=plan([{"day":0,"direction":"outflow","amount":"1000","currency":"USD","label":"Cost"},
            {"day":10,"direction":"inflow","amount":"1200","currency":"USD","label":"Buyer payment"}])
    r=evaluate_cashflow(p,FxBook(target_currency="USD",rates=[]),NOW)
    assert r["peak_funding"]=="1000.00" and r["financing_cost_estimate"]=="10.00"
    assert r["ending_balance"]=="200.00" and r["included_in_quote"] is False


def test_prepayment_reduces_funding():
    p=plan([{"day":0,"direction":"inflow","amount":"1200","currency":"USD","label":"Deposit"},
            {"day":2,"direction":"outflow","amount":"1000","currency":"USD","label":"Cost"}])
    assert evaluate_cashflow(p,FxBook(target_currency="USD",rates=[]),NOW)["peak_funding"]=="0.00"


def test_unknown_cashflow_fx_rejected():
    p=plan([{"day":0,"direction":"outflow","amount":"1000","currency":"EUR","label":"Cost"}])
    with pytest.raises(DomainError):evaluate_cashflow(p,FxBook(target_currency="USD",rates=[]),NOW)


def test_cashflow_horizon_enforced():
    p=plan([{"day":21,"direction":"outflow","amount":"1000","currency":"USD","label":"Cost"}])
    with pytest.raises(DomainError):evaluate_cashflow(p,FxBook(target_currency="USD",rates=[]),NOW)
