"""Strict public contracts. All monetary values are plain decimal strings."""
from __future__ import annotations
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Text = Annotated[str, Field(strict=True, min_length=1, max_length=500)]
Id = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")]
Amount = Annotated[str, Field(strict=True, pattern=r"^(0|[1-9][0-9]{0,14})(\.[0-9]{1,8})?$")]
Rate = Annotated[str, Field(strict=True, pattern=r"^(0(\.[0-9]{1,12})?|1(\.0{1,12})?)$")]
FxRate = Annotated[str, Field(strict=True, pattern=r"^(0|[1-9][0-9]{0,6})(\.[0-9]{1,12})?$")]
Hash = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
Currency = Literal["USD", "CNY", "EUR", "GBP", "JPY", "CAD", "AUD", "AED", "KWD"]
Term = Literal["EXW", "FCA", "FOB", "CIF", "DAP", "DDP"]
Category = Literal[
    "product", "packaging", "origin_loading", "origin_transport", "export_clearance",
    "export_handling", "main_carriage", "insurance", "destination_handling",
    "destination_transport", "import_clearance", "import_duties", "import_taxes", "other"
]


class Evidence(Contract):
    reference: Text
    sha256: Hash
    observed_at: AwareDatetime
    valid_until: AwareDatetime
    kind: Literal["supplier_quote", "freight_quote", "cost_sheet", "financial_policy", "manual_confirmation"]

    @model_validator(mode="after")
    def dates(self):
        if self.valid_until <= self.observed_at:
            raise ValueError("evidence validity must end after observation")
        return self


class CaseInput(Contract):
    external_ref: Text
    customer_name: Text
    product_id: Id
    product_revision: Id
    description: Text
    quantity: Amount
    unit: Literal["piece", "meter", "kilogram", "set", "pair"]
    currency: Currency
    incoterm: Term
    incoterms_edition: Literal["2020"] = "2020"
    named_place: Text
    transport: Literal["sea", "inland_waterway", "air", "road", "rail", "multimodal"]
    fca_delivery: Literal["seller_premises", "other_place"] | None = None
    payment_terms: Text
    valid_until: AwareDatetime
    max_lead_days: Annotated[int, Field(strict=True, ge=1, le=730)] | None = None

    @model_validator(mode="after")
    def semantic(self):
        if Decimal(self.quantity) <= 0:
            raise ValueError("quantity must be positive")
        if self.unit in ("piece", "set", "pair") and Decimal(self.quantity) != Decimal(self.quantity).to_integral_value():
            raise ValueError("counted units require whole-number quantities")
        if self.incoterm == "FCA" and self.fca_delivery is None:
            raise ValueError("FCA requires a specified delivery variant")
        if self.incoterm != "FCA" and self.fca_delivery is not None:
            raise ValueError("fca_delivery applies only to FCA")
        return self


class CostLine(Contract):
    id: Id
    label: Text
    category: Category
    amount: Amount
    currency: Currency
    basis: Literal["per_unit", "fixed"]
    unit: Literal["piece", "meter", "kilogram", "set", "pair"] | None = None
    payer: Literal["seller", "buyer"] = "seller"
    covers: list[Category] = Field(default_factory=list, max_length=14)
    evidence: Evidence

    @model_validator(mode="after")
    def semantic(self):
        if self.basis == "per_unit" and self.unit is None:
            raise ValueError("per-unit cost must specify its unit")
        if self.basis == "fixed" and self.unit is not None:
            raise ValueError("fixed cost must not specify a unit")
        if self.category in self.covers or len(set(self.covers)) != len(self.covers):
            raise ValueError("bundle coverage must be unique and exclude its primary category")
        return self


class CostBook(Contract):
    product_id: Id
    product_revision: Id
    incoterm: Term
    named_place: Text
    transport: Literal["sea", "inland_waterway", "air", "road", "rail", "multimodal"]
    fca_delivery: Literal["seller_premises", "other_place"] | None = None
    payment_terms: Text
    quantity_min: Amount
    quantity_max: Amount
    unit: Literal["piece", "meter", "kilogram", "set", "pair"]
    lead_time_days: Annotated[int, Field(strict=True, ge=1, le=730)]
    tax_basis: Literal["net_nonrecoverable_confirmed"]
    scope_note: Text
    evidence: Evidence
    lines: list[CostLine] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def semantic(self):
        if not (Decimal("0") < Decimal(self.quantity_min) <= Decimal(self.quantity_max)):
            raise ValueError("invalid quantity range")
        if len({x.id for x in self.lines}) != len(self.lines):
            raise ValueError("duplicate cost line ids")
        if (self.incoterm == "FCA") != (self.fca_delivery is not None):
            raise ValueError("FCA delivery variant must match the selected term")
        return self


class FxItem(Contract):
    source_currency: Currency
    rate: FxRate
    evidence: Evidence

    @model_validator(mode="after")
    def positive(self):
        if Decimal(self.rate) <= 0:
            raise ValueError("exchange rate must be positive")
        return self


class FxBook(Contract):
    target_currency: Currency
    rates: list[FxItem] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def distinct(self):
        if len({x.source_currency for x in self.rates}) != len(self.rates):
            raise ValueError("duplicate FX currency")
        if any(x.source_currency == self.target_currency for x in self.rates):
            raise ValueError("do not provide identity FX rates")
        return self


class Policy(Contract):
    name: Text
    minimum_margin: Rate
    target_margin: Rate
    revenue_fee_rate: Rate
    unit_price_decimals: Annotated[int, Field(strict=True, ge=2, le=6)] = 4
    fee_note: Text
    evidence: Evidence

    @model_validator(mode="after")
    def margins(self):
        low, target, fee = map(Decimal, [self.minimum_margin, self.target_margin, self.revenue_fee_rate])
        if low > target or target + fee >= 1:
            raise ValueError("require minimum <= target and target + fee < 1")
        return self


class NewCase(Contract):
    request_id: Id
    case: CaseInput


class ReviseCase(NewCase):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]


class SaveCostBook(Contract):
    request_id: Id
    cost_book: CostBook


class SaveFxBook(Contract):
    request_id: Id
    fx_book: FxBook


class SavePolicy(Contract):
    request_id: Id
    policy: Policy


class RevokeSnapshot(Contract):
    request_id: Id
    reason: Text


class Calculation(Contract):
    request_id: Id
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    cost_snapshot_id: Id
    fx_snapshot_id: Id
    policy_snapshot_id: Id
    proposed_unit_price: Amount | None = None

    @model_validator(mode="after")
    def positive(self):
        if self.proposed_unit_price is not None and Decimal(self.proposed_unit_price) <= 0:
            raise ValueError("proposed price must be positive")
        return self


class QuoteAction(Contract):
    request_id: Id
    expected_version: Annotated[int, Field(strict=True, ge=1)]
    content_hash: Hash


class Approval(QuoteAction):
    decision: Literal["approve", "reject"]
    reason: Text


class Scenario(Contract):
    label: Text
    quantity: Amount
    cost_snapshot_id: Id
    proposed_unit_price: Amount | None = None
    incoterm: Term | None = None
    named_place: Text | None = None
    transport: Literal["sea", "inland_waterway", "air", "road", "rail", "multimodal"] | None = None
    fca_delivery: Literal["seller_premises", "other_place"] | None = None
    payment_terms: Text | None = None

    @model_validator(mode="after")
    def explicit_terms(self):
        if self.incoterm is not None and (self.named_place is None or self.transport is None):
            raise ValueError("a term alternative requires an explicit named place and transport mode")
        return self


class CompareInput(Contract):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    fx_snapshot_id: Id
    policy_snapshot_id: Id
    scenarios: list[Scenario] = Field(min_length=1, max_length=20)


class CashEvent(Contract):
    day: Annotated[int, Field(strict=True, ge=0, le=730)]
    direction: Literal["inflow", "outflow"]
    amount: Amount
    currency: Currency
    label: Text


class CashPlan(Contract):
    fx_snapshot_id: Id
    annual_rate: Rate
    horizon_days: Annotated[int, Field(strict=True, ge=1, le=730)]
    evidence: Evidence
    events: list[CashEvent] = Field(min_length=1, max_length=500)
