"""Conservative cost-completeness checklists, NOT a full reproduction of ICC rules."""
RULE_VERSION = "seller-cost-checklist-2020-v1"
BASE = {"product", "packaging"}
FOB = BASE | {"origin_loading", "origin_transport", "export_clearance", "export_handling"}


def required_categories(term: str, fca_delivery: str | None = None) -> set[str]:
    if term == "EXW":
        return BASE.copy()
    if term == "FCA":
        if fca_delivery == "seller_premises":
            return BASE | {"origin_loading", "export_clearance"}
        return BASE | {"origin_transport", "export_clearance", "export_handling"}
    if term == "FOB":
        return FOB.copy()
    if term == "CIF":
        return FOB | {"main_carriage", "insurance"}
    if term in ("DAP", "DDP"):
        return FOB | {"main_carriage", "destination_handling", "destination_transport"} | (
            {"import_clearance", "import_duties", "import_taxes"} if term == "DDP" else set())
    raise ValueError("unsupported term")
