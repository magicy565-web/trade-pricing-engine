from .helpers import *
from .test_api import setup_api, auth
from trade_pricing.core.money import digest


def test_supplier_import_confirm_and_revision_conflict(tmp_path):
    client,svc,c,req=setup_api(tmp_path)
    r={'id':'supplier-quote-1','quote_request_id':'request-1','supplier_id':'supplier-1','version':1,
       'unit_price':'10','currency':'USD','moq':50,'lead_time_days':20,'trade_term':'EXW',
       'quote_valid_until':evidence()['valid_until'],'status':'submitted','payment_terms':'100% prepayment'}
    e=evidence();e['sha256']=digest(r)
    body={'request_id':'import','source':'buyermcp-export','record':r,'evidence':e}
    response=client.post('/v1/supplier-quotes',headers=auth(),json=body)
    assert response.status_code==201,response.text
    imported=response.json();sid=imported['id']
    assert len(svc.list_snapshots(AGENT))==3
    co=costs_data();co['lines'][0]['evidence']=e
    confirm={'request_id':'confirm','source_hash':digest(r),'cost_book':co}
    assert client.post(f'/v1/supplier-quotes/{sid}/confirm',headers=auth(),json=confirm).status_code==403
    result=client.post(f'/v1/supplier-quotes/{sid}/confirm',headers=auth('admin'),json=confirm)
    assert result.status_code==201,result.text
    assert len(svc.list_snapshots(AGENT))==4
    bad={**body,'request_id':'changed','record':{**r,'unit_price':'11'}}
    bad['evidence']={**e,'sha256':digest(bad['record'])}
    assert client.post('/v1/supplier-quotes',headers=auth(),json=bad).status_code==409
    bad['record']['version']=2;bad['record']['id']='supplier-quote-2';bad['evidence']['sha256']=digest(bad['record'])
    assert client.post('/v1/supplier-quotes',headers=auth(),json=bad).status_code==201
    confirm['request_id']='confirm-old'
    assert client.post(f'/v1/supplier-quotes/{sid}/confirm',headers=auth('admin'),json=confirm).status_code==409
    assert client.get(f'/v1/supplier-quotes/{sid}',headers=auth()).json()['latest_known_version']==2


def test_untrusted_numeric_price_rejected(tmp_path):
    client,*_=setup_api(tmp_path)
    r={'id':'q','quote_request_id':'r','supplier_id':'s','version':1,'unit_price':1.2,'currency':'USD','moq':1,'lead_time_days':1,'trade_term':'EXW','quote_valid_until':evidence()['valid_until'],'status':'submitted'}
    e=evidence();e['sha256']=digest(r)
    response=client.post('/v1/supplier-quotes',headers=auth(),json={'request_id':'bad','source':'portal','record':r,'evidence':e})
    assert response.status_code==422
