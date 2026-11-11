"""MCP 2025-11-25 tools-only stdio compatibility adapter.

Does not own the database or approval credentials. No sampling, OAuth, arbitrary
HTTP proxying, filesystem access, prompts or remote Streamable HTTP are exposed.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError, URLError
from .. import __version__
from ..models import Contract, Id, NewCase, Calculation, CompareInput, QuoteAction, CashPlan
from ..errors import DomainError

SUPPORTED = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
MAX_LINE = 1_048_576


class CaseId(Contract):
    case_id: Id


class QuoteId(Contract):
    quote_id: Id


class CalculateArgs(Calculation):
    case_id: Id


class CompareArgs(CompareInput):
    case_id: Id


class SubmitArgs(QuoteAction):
    quote_id: Id


class Empty(Contract):
    pass


TOOLS = {
    "create_pricing_case": (NewCase, "Create a real customer quotation case; does not send anything.", False),
    "list_pricing_cases": (Empty, "List this enterprise's pricing cases.", True),
    "get_pricing_case": (CaseId, "Read a case and its immutable quote revisions.", True),
    "list_cost_evidence": (Empty, "List finance-confirmed cost, FX and policy snapshots. This tool cannot confirm evidence.", True),
    "calculate_quote": (CalculateArgs, "Calculate with confirmed snapshots; return blockers instead of invented costs.", False),
    "compare_scenarios": (CompareArgs, "Compare supplied quantity/price alternatives against evidence-backed cost ranges.", True),
    "submit_quote_approval": (SubmitArgs, "Submit an exact quote hash for separate human approval. Never approves.", False),
    "get_quote": (QuoteId, "Read internal calculations and current freshness checks.", True),
    "replay_quote": (QuoteId, "Recalculate a historical quote with its frozen inputs and original clock, without granting approval.", True),
    "get_customer_quote": (QuoteId, "Read allowlisted customer fields only after human approval. Does not send.", True),
    "evaluate_cashflow": (CashPlan, "Estimate funding from an explicitly supplied cash schedule, not accounting profit.", True),
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApiClient:
    def __init__(self, url: str, token: str):
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
            raise ValueError("MCP requires an exact trusted API origin")
        if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("HTTP is permitted only for loopback")
        if not token or len(token) > 512:
            raise ValueError("Invalid token file")
        self.url, self.token = url.rstrip("/"), token
        self.opener = build_opener(NoRedirect, ProxyHandler({}))
        me = self.call("GET", "/v1/me")
        if me.get("role") != "agent":
            raise ValueError("MCP must use the agent credential, never an admin/approver credential")

    def call(self, method, path, data=None):
        body = json.dumps(data, allow_nan=False).encode() if data is not None else None
        request = Request(self.url + path, data=body, method=method,
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(4 * MAX_LINE + 1)
                if len(raw) > 4 * MAX_LINE:
                    raise DomainError("RESPONSE_TOO_LARGE", "API response exceeds adapter limit")
                return json.loads(raw)
        except HTTPError as exc:
            try:
                problem = json.loads(exc.read(MAX_LINE)).get("error", {})
            except (ValueError, UnicodeError):
                problem = {}
            raise DomainError(problem.get("code", "API_ERROR"), problem.get("message", f"API returned {exc.code}"), exc.code) from None
        except URLError:
            raise DomainError("API_UNAVAILABLE", "Pricing service unreachable; do not assume the operation failed or repeat with a new request_id", 503) from None

    def invoke(self, name, args):
        if name not in TOOLS:
            raise DomainError("UNKNOWN_TOOL", "Unknown tool")
        model = TOOLS[name][0].model_validate(args)
        d = model.model_dump(mode="json")
        case_id = quote(d.pop("case_id"), safe="") if "case_id" in d else None
        quote_id = quote(d.pop("quote_id"), safe="") if "quote_id" in d else None
        routes = {
            "create_pricing_case": ("POST", "/v1/cases"),
            "list_pricing_cases": ("GET", "/v1/cases"),
            "get_pricing_case": ("GET", f"/v1/cases/{case_id}"),
            "list_cost_evidence": ("GET", "/v1/snapshots"),
            "calculate_quote": ("POST", f"/v1/cases/{case_id}/calculate"),
            "compare_scenarios": ("POST", f"/v1/cases/{case_id}/compare"),
            "submit_quote_approval": ("POST", f"/v1/quotes/{quote_id}/submit"),
            "get_quote": ("GET", f"/v1/quotes/{quote_id}"),
            "replay_quote": ("GET", f"/v1/quotes/{quote_id}/replay"),
            "get_customer_quote": ("GET", f"/v1/quotes/{quote_id}/customer"),
            "evaluate_cashflow": ("POST", "/v1/cashflow"),
        }
        method, path = routes[name]
        return self.call(method, path, d if method == "POST" else None)


class Protocol:
    def __init__(self, client):
        self.client, self.initialized, self.ready = client, False, False

    def handle(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return self.error(None, -32600, "Invalid JSON-RPC request")
        mid, method = message.get("id"), message["method"]
        if "id" in message and (not isinstance(mid, (str, int)) or isinstance(mid, bool)):
            return self.error(None, -32600, "Invalid request id")
        params = message.get("params", {})
        if not isinstance(params, dict):
            return None if "id" not in message else self.error(mid, -32602, "params must be an object")
        if "id" not in message:
            if method == "notifications/initialized" and self.initialized:
                self.ready = True
            return None
        if method == "initialize":
            if self.initialized:
                return self.error(mid, -32600, "Connection is already initialized")
            if not isinstance(params.get("protocolVersion"), str) or not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
                return self.error(mid, -32602, "Missing initialization parameters")
            requested = params["protocolVersion"]
            chosen = requested if requested in SUPPORTED else SUPPORTED[0]
            self.initialized = True
            return self.result(mid, {"protocolVersion": chosen, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "trade-pricing-engine", "version": __version__},
                "instructions": "Use confirmed finance snapshots. Never invent evidence or margins. Approval and sending are outside this tool surface."})
        if method == "ping":
            return self.result(mid, {})
        if not self.ready:
            return self.error(mid, -32000, "Initialize and send notifications/initialized before using tools")
        if method == "tools/list":
            return self.result(mid, {"tools": [{"name": name, "description": desc, "inputSchema": model.model_json_schema(),
                "annotations": {"readOnlyHint": readonly, "destructiveHint": False,
                                "idempotentHint": True, "openWorldHint": False}}
                for name, (model, desc, readonly) in TOOLS.items()]})
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in TOOLS:
                return self.error(mid, -32602, "Unknown tool")
            from pydantic import ValidationError
            try:
                result = self.client.invoke(name, params.get("arguments", {}))
                return self.result(mid, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                                         "structuredContent": result, "isError": False})
            except ValidationError as exc:
                details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                return self.result(mid, {"isError": True, "content": [{"type": "text", "text": "VALIDATION: " + details}]})
            except DomainError as exc:
                return self.result(mid, {"isError": True, "content": [{"type": "text", "text": f"{exc.code}: {exc.message}"}]})
        return self.error(mid, -32601, "Method not found")

    @staticmethod
    def result(mid, data):
        return {"jsonrpc": "2.0", "id": mid, "result": data}

    @staticmethod
    def error(mid, code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def run_stdio(url: str, token_file: Path):
    client = ApiClient(url, token_file.read_text(encoding="utf-8").strip())
    protocol = Protocol(client)
    while True:
        raw = sys.stdin.buffer.readline(MAX_LINE + 1)
        if not raw:
            break
        if len(raw) > MAX_LINE:
            print("Oversized MCP message; closing stream", file=sys.stderr)
            break
        try:
            value = json.loads(raw.decode("utf-8"))
            response = protocol.handle(value)
        except (ValueError, UnicodeError):
            response = Protocol.error(None, -32700, "Parse error")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
            sys.stdout.flush()
