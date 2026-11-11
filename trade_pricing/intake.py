"""Supplier portal exports remain unconfirmed until reviewed by a cost controller."""
import json
from decimal import Decimal
from typing import Annotated
from pydantic import Field
from .models import Contract, Id, Text, Amount, Currency, Evidence, CostBook
from .core.money import canonical, digest
from .service import require, identifier
from .errors import DomainError


class ImportSupplierQuote(Contract):
    request_id: Id
    source: Text
    # Exact exported record, retained for audit; never fetch a caller-controlled URL.
    record: dict
    evidence: Evidence


class ConfirmSupplierCost(Contract):
    request_id: Id
    source_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    cost_book: CostBook


def initialize(store):
    with store.connection() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS supplier_intake(
            id TEXT PRIMARY KEY, tenant TEXT NOT NULL, source TEXT NOT NULL,
            request_ref TEXT NOT NULL, supplier_ref TEXT NOT NULL, version INTEGER NOT NULL,
            hash TEXT NOT NULL, payload TEXT NOT NULL,
            UNIQUE(tenant,source,request_ref,supplier_ref,version))''')


def import_quote(svc, actor, req):
    require(actor,'admin','agent')
    r=req.record
    # Portal uses a new id on each revision: the stream is request + supplier.
    required=('id','quote_request_id','supplier_id','version','unit_price','currency','moq','lead_time_days','trade_term','quote_valid_until','status')
    if any(k not in r for k in required):
        raise DomainError('SOURCE_FIELDS','Incomplete supplier portal export',422)
    from pydantic import TypeAdapter, ValidationError, AwareDatetime
    try:
        for key in ('id','quote_request_id','supplier_id'):TypeAdapter(Id).validate_python(r[key])
        TypeAdapter(Amount).validate_python(r['unit_price'])
        TypeAdapter(Currency).validate_python(r['currency'])
        expiry=TypeAdapter(AwareDatetime).validate_python(r['quote_valid_until'])
        for key in ('version','moq','lead_time_days'):
            if type(r[key]) is not int or r[key]<1:raise ValueError(key)
        if Decimal(r['unit_price'])<=0:raise ValueError('price')
        if r['status'] not in ('submitted','updated','expired','withdrawn'):raise ValueError('status')
    except (ValidationError,ValueError):
        raise DomainError('SOURCE_VALIDATION','Require decimal string price, supported currency, positive integers and timezone-aware expiry; do not infer missing timezone',422) from None
    payload={'record':r,'evidence':req.evidence.model_dump(mode='json')}
    content_hash=digest(r)
    if req.evidence.sha256 != content_hash:
        raise DomainError('SOURCE_HASH','Evidence hash must match canonical JSON of the exported record',422)
    def action(con):
        old=con.execute('SELECT * FROM supplier_intake WHERE tenant=? AND source=? AND request_ref=? AND supplier_ref=? AND version=?',
            (actor.tenant,req.source,r['quote_request_id'],r['supplier_id'],r['version'])).fetchone()
        if old:
            if old['hash']!=content_hash:raise DomainError('SOURCE_VERSION_CONFLICT','Same source version has different content',409)
            return {'id':old['id'],'source_hash':content_hash,'state':'unconfirmed'}
        sid=identifier('intake')
        con.execute('INSERT INTO supplier_intake VALUES(?,?,?,?,?,?,?,?)',(sid,actor.tenant,req.source,r['quote_request_id'],r['supplier_id'],r['version'],content_hash,canonical(payload)))
        return {'id':sid,'source_hash':content_hash,'state':'unconfirmed'}
    return svc._mutate(actor,req,'supplier.import',action)


def get_intake(svc,actor,sid):
    with svc.store.connection() as con:
        row=con.execute('SELECT * FROM supplier_intake WHERE id=? AND tenant=?',(sid,actor.tenant)).fetchone()
        if row is None:raise DomainError('NOT_FOUND','Supplier intake not found',404)
        p=json.loads(row['payload'])
        if digest(p['record'])!=row['hash']:raise DomainError('SOURCE_INTEGRITY','Stored export hash mismatch',409)
        newest=con.execute('SELECT MAX(version) FROM supplier_intake WHERE tenant=? AND source=? AND request_ref=? AND supplier_ref=?',
            (actor.tenant,row['source'],row['request_ref'],row['supplier_ref'])).fetchone()[0]
        affected=[]
        for snapshot in con.execute("SELECT id,payload,revoked_at FROM snapshots WHERE tenant=? AND kind='cost'",(actor.tenant,)):
            data=json.loads(snapshot['payload'])
            if any(line['evidence']['sha256']==row['hash'] for line in data['lines']):
                affected.append({'id':snapshot['id'],'revoked_at':snapshot['revoked_at']})
        snapshot_ids={item['id'] for item in affected}
        quotes=[]
        for quote in con.execute('SELECT id,payload,state FROM quotes WHERE tenant=?',(actor.tenant,)):
            if json.loads(quote['payload'])['cost_snapshot_id'] in snapshot_ids:
                quotes.append({'id':quote['id'],'state':quote['state']})
        return {'id':sid,'source_hash':row['hash'],'latest_known_version':newest,
                'affected_cost_snapshots':affected,'affected_quotes':quotes,
                'review_required':newest!=row['version'],**p,
                'missing_context':['product_id','product_revision','unit','named_place','transport','quantity_max','tax_basis','complete seller cost breakdown'],
                'warning':'Imported export is not supplier authentication or confirmed cost. Source timestamps without timezone must be corrected upstream.'}


def confirm_cost(svc,actor,sid,req):
    require(actor,'admin')
    def action(con):
        # Same connection/transaction: a newer revision cannot arrive during confirmation.
        row=con.execute('SELECT * FROM supplier_intake WHERE id=? AND tenant=?',(sid,actor.tenant)).fetchone()
        if row is None:raise DomainError('NOT_FOUND','Supplier intake not found',404)
        p=json.loads(row['payload']);r=p['record']
        latest=con.execute('SELECT MAX(version) FROM supplier_intake WHERE tenant=? AND source=? AND request_ref=? AND supplier_ref=?',
            (actor.tenant,row['source'],row['request_ref'],row['supplier_ref'])).fetchone()[0]
        if row['hash']!=req.source_hash or digest(r)!=req.source_hash:raise DomainError('SOURCE_HASH','Source changed',409)
        from pydantic import TypeAdapter, AwareDatetime
        if latest!=r['version'] or r['status'] not in ('submitted','updated') or TypeAdapter(AwareDatetime).validate_python(r['quote_valid_until'])<=svc.clock():
            raise DomainError('SOURCE_STALE','Supplier quote is outdated, expired or withdrawn',409)
        cost=req.cost_book
        if Decimal(cost.quantity_min)<r['moq'] or cost.lead_time_days<r['lead_time_days']:
            raise DomainError('SOURCE_SCOPE','Cost scope violates supplier MOQ or lead time',422)
        if cost.payment_terms!=r.get('payment_terms'):
            raise DomainError('SOURCE_SCOPE','Supplier payment terms require explicit matching confirmation',422)
        # First adapter intentionally supports EXW only: freight-inclusive prices need allocation.
        if r['trade_term']!='EXW' or cost.incoterm!='EXW':raise DomainError('SOURCE_TERM','This adapter supports EXW only; do not treat a bundled delivered price as product cost',422)
        linked=[line for line in cost.lines if line.category=='product' and line.evidence.sha256==req.source_hash]
        if len(linked)!=1 or linked[0].amount!=r['unit_price'] or linked[0].currency!=r['currency'] or linked[0].basis!='per_unit' or linked[0].payer!='seller':
            raise DomainError('SOURCE_PRICE','Require exactly one source-linked seller per-unit product line matching the original price and currency',422)
        if linked[0].evidence.valid_until>TypeAdapter(AwareDatetime).validate_python(r['quote_valid_until']):
            raise DomainError('SOURCE_VALIDITY','Cost evidence cannot outlive supplier quote',422)
        value=cost.model_dump(mode='json');snapshot=identifier('cost');now=svc.clock().isoformat()
        con.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,NULL,NULL)',(snapshot,actor.tenant,'cost',canonical(value),digest(value),actor.name,now))
        return {'id':snapshot,'kind':'cost','source_intake_id':sid,'source_hash':req.source_hash,'confirmed_by':actor.name}
    return svc._mutate(actor,req,f'supplier.confirm:{sid}',action)
