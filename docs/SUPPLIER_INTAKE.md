# Supplier intake increment after v0.2

Implemented against the actual `buyermcp/sourcing_mvp/quote_service.py` export shape. This is an authenticated export-import API, not a live connection to a deployed supplier server. No real supplier data has been imported.

1. POST `/v1/supplier-quotes` with `request_id`, `source` (stable source identifier), `record` (the exact portal export), and `evidence`. admin or agent can import. Import never creates an approved cost snapshot.
2. GET `/v1/supplier-quotes/{id}` returns original content, latest known stream version, missing cost context and affected cost/quote IDs.
3. admin POST `/v1/supplier-quotes/{id}/confirm` with `request_id`, `source_hash`, and the complete reviewed `cost_book` creates a cost snapshot.

Hash is SHA256 of UTF-8 JSON with sorted keys, compact separators, ensure_ascii=False and allow_nan=False (the engine's canonical/digest functions). It verifies retained content, not supplier identity. The importer does not fetch URLs. Exports must exclude credentials and personal fields not needed for pricing. Prices must be decimal strings and expiry must contain an explicit timezone; do not infer the timezone of old portal timestamps.

A stream is source + quote_request_id + supplier_id, because the portal generates a new quote id on each revision. Reimporting identical content is safe; different content under the same version is rejected. Older imports are retained, but cannot be newly confirmed after a newer version is known.

The initial confirmation adapter supports EXW only. The controller supplies product identity/revision, unit, named place, transport, supported quantity upper limit, tax treatment and the complete cost breakdown. Exactly one seller per-unit product line must reference the source hash and match price/currency. MOQ, lead time, payment terms and evidence expiry are checked. Other delivery terms need a separate explicit allocation design.

A newer unconfirmed import does NOT automatically revoke previously confirmed costs: an agent cannot unilaterally withdraw finance's decision. Use the impact list to review and revoke affected cost snapshots through the existing admin endpoint; that blocks related quote exports. Until review, previously confirmed snapshots retain their existing validity. This is a known manual control, not automatic source-change protection.

Remaining work: deployed source authentication/transport, an export normalization step with explicit timezone confirmation, UI import/review controls, and actual enterprise acceptance. Existing JSON/HTTP clients can use the endpoints now. No production integration or browser acceptance is claimed.

GitHub Actions is now manual-only (`workflow_dispatch`) because the owner has no Actions allowance. Run `python -m pytest -q` and `python scripts/smoke.py` locally instead; no workflow dispatch is performed by this delivery.
