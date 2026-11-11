from trade_pricing.adapters.mcp import Protocol,TOOLS
import pytest


class FakeAPI:
    """Transport-isolation test double, never production data or a claimed host integration."""
    def invoke(self,name,args):
        if name not in TOOLS:raise AssertionError("unexpected tool")
        TOOLS[name][0].model_validate(args)
        return {"fixture":True,"cases":[]}


def ready():
    p=Protocol(FakeAPI())
    r=p.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0"}}})
    assert r['result']['protocolVersion']=='2025-11-25'
    assert p.handle({"jsonrpc":"2.0","method":"notifications/initialized"}) is None
    return p


def test_lifecycle_and_tool_discovery():
    p=ready();r=p.handle({"jsonrpc":"2.0","id":2,"method":"tools/list"})
    names={x['name'] for x in r['result']['tools']}
    assert len(names)==11 and 'calculate_quote' in names
    assert all('approve' not in name and 'finalize' not in name and 'confirm' not in name for name in names)


def test_tool_call_roundtrip():
    r=ready().handle({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_pricing_cases","arguments":{}}})
    assert r['result']['isError'] is False
    assert r['result']['structuredContent']['fixture'] is True


def test_methods_before_initialize_rejected():
    r=Protocol(FakeAPI()).handle({"jsonrpc":"2.0","id":1,"method":"tools/list"})
    assert r['error']['code']==-32000


def test_illegal_approval_tool_does_not_exist():
    r=ready().handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"approve_quote","arguments":{}}})
    assert r['error']['code']==-32602


def test_invalid_input_is_a_tool_error():
    r=ready().handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_quote","arguments":{"quote_id":"../../secret"}}})
    assert r['result']['isError'] is True


@pytest.mark.parametrize('message',[[],{},"bad",{"jsonrpc":"1.0","id":1,"method":"ping"},{"jsonrpc":"2.0","id":True,"method":"ping"}])
def test_invalid_rpc(message):
    assert ready().handle(message)['error']['code']==-32600


def test_notification_has_no_reply():
    assert ready().handle({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":99}}) is None


def test_unsupported_version_negotiates_explicit_legacy_version():
    p=Protocol(FakeAPI())
    r=p.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2099-01-01","capabilities":{},"clientInfo":{"name":"test","version":"0"}}})
    assert r['result']['protocolVersion']=='2025-11-25'


def test_repeated_initialization_rejected():
    assert ready().handle({"jsonrpc":"2.0","id":8,"method":"initialize","params":{}})['error']['code']==-32600
