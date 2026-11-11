"""Customer documents use an allowlist and HTML-escape every untrusted value."""
from html import escape


def render_quote(public: dict) -> str:
    def e(key):
        return escape(str(public[key]), quote=True)
    draft = "APPROVED DRAFT · NOT FINALIZED" if public["state"] == "approved" else "QUOTATION · NOT SENT"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Quotation {e('quote_number')}</title>
<style>body{{font:16px system-ui,sans-serif;max-width:860px;margin:48px auto;padding:24px;color:#18232d}}
h1{{font-size:36px}}.tag{{font-size:12px;letter-spacing:1px;color:#5b6570}}table{{width:100%;border-collapse:collapse;margin:28px 0}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid #d6dde3}}footer{{font-size:12px;margin-top:36px;color:#586773}}
@media(max-width:600px){{body{{margin:0;padding:18px}}table{{font-size:12px}}th,td{{padding:6px}}}}
</style></head><body><div class="tag">{draft}</div><h1>{e('seller')}</h1>
<p>Prepared for <strong>{e('customer')}</strong></p><p>Quotation: {e('quote_number')}</p>
<table><thead><tr><th>Description</th><th>Quantity</th><th>Unit price</th><th>Total</th></tr></thead>
<tbody><tr><td>{e('description')}<br>{e('product_id')} · Revision {e('product_revision')}</td><td>{e('quantity')} {e('unit')}</td>
<td>{e('currency')} {e('unit_price')}</td><td>{e('currency')} {e('total')}</td></tr></tbody></table>
<p><strong>Delivery:</strong> {e('incoterm')} {e('named_place')}, Incoterms® {e('edition')}</p>
<p><strong>Lead time:</strong> {e('lead_time_days')} days, subject to the agreed order/start conditions.</p>
<p><strong>Payment terms:</strong> {e('payment_terms')}</p><p><strong>Valid until:</strong> {e('valid_until')}</p>
<footer>{e('note')}<p>No email or message has been sent by this system.</p></footer></body></html>'''

