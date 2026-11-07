"""Exercises the MCP tools through a real in-process MCP client, with a fake Fusion bridge."""

import asyncio
import json

from mcp.client.client import Client

from fusion_mcp.bridge import BridgeError
from fusion_mcp.server import create_server


class FakeBridge:
    def __init__(self):
        self.params = {"width": "20 mm", "d1": "5 mm"}

    async def call(self, method, **p):
        if method == "document_info":
            return {"document": "Bracket v3", "units": "mm", "bodies": 1}
        if method == "list_parameters":
            return {"parameters": [{"name": k, "expression": v, "user": k == "width"} for k, v in self.params.items()]}
        if method == "set_parameter":
            if p["name"] not in self.params:
                raise BridgeError(f"KeyError: No parameter named {p['name']!r}")
            before, self.params[p["name"]] = self.params[p["name"]], p["expression"]
            return {"name": p["name"], "before": before, "after": p["expression"]}
        if method == "export":
            await asyncio.sleep(0.05)
            return {"path": p["path"], "format": p["format"], "bytes": 1234}
        raise BridgeError(f"unknown method {method}")


def data(result):
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


async def test_tools_listed_with_annotations():
    async with Client(create_server(FakeBridge())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == {"get_design_info", "list_parameters", "set_parameter", "start_export", "get_job"}
    assert tools["get_design_info"].annotations.read_only_hint is True
    assert tools["set_parameter"].annotations.read_only_hint is False


async def test_read_and_change_parameters():
    bridge = FakeBridge()
    async with Client(create_server(bridge)) as c:
        info = data(await c.call_tool("get_design_info", {}))
        assert info["document"] == "Bracket v3"
        users = data(await c.call_tool("list_parameters", {"user_only": True}))
        assert [p["name"] for p in users] == ["width"]
        changed = data(await c.call_tool("set_parameter", {"name": "width", "expression": "25 mm"}))
        assert changed["before"] == "20 mm" and bridge.params["width"] == "25 mm"
        bad = await c.call_tool("set_parameter", {"name": "nope", "expression": "1 mm"})
        assert bad.is_error


async def test_export_runs_as_a_job():
    async with Client(create_server(FakeBridge())) as c:
        job = data(await c.call_tool("start_export", {"format": "step", "path": "/tmp/bracket.step"}))
        assert job["status"] == "working"
        for _ in range(50):
            status = data(await c.call_tool("get_job", {"job_id": job["job_id"]}))
            if status["status"] != "working":
                break
            await asyncio.sleep(0.02)
        assert status["status"] == "completed" and status["result"]["bytes"] == 1234
