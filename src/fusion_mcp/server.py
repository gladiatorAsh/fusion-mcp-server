"""fusion-mcp-server: MCP tools for Autodesk Fusion.

Quick operations (reading the design, changing a parameter) return directly. Exports can take minutes
on large assemblies, so they run as jobs: `start_export` returns a job id at once and `get_job` reports
progress. That keeps every request short, which suits the stateless 2026-07-28 protocol, and maps
one-to-one onto the MCP Tasks extension once the Python SDK supports it (see the roadmap).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .bridge import FusionBridge

READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)


@dataclass
class Job:
    id: str
    kind: str
    status: Literal["working", "completed", "failed"] = "working"
    started: float = field(default_factory=time.time)
    finished: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def view(self) -> dict[str, Any]:
        end = self.finished or time.time()
        return {"job_id": self.id, "kind": self.kind, "status": self.status, "elapsed_s": round(end - self.started, 1), "result": self.result, "error": self.error}


def create_server(bridge: FusionBridge | None = None) -> MCPServer:
    bridge = bridge or FusionBridge()
    jobs: dict[str, Job] = {}
    server = MCPServer(
        "fusion-mcp-server",
        version="0.1.0",
        instructions="Tools for reading and editing the active Autodesk Fusion design. Read the design and its parameters before changing anything.",
    )

    @server.tool(description="Summarize the active Fusion design: document name, units, and counts of components, bodies, sketches, features and user parameters.", annotations=READ)
    async def get_design_info() -> dict[str, Any]:
        return await bridge.call("document_info")

    @server.tool(description="List every parameter in the active design with its expression, evaluated value and unit. Use this before changing dimensions.", annotations=READ)
    async def list_parameters(
        user_only: bool = Field(False, description="Only return user parameters, not model parameters created by features"),
    ) -> list[dict[str, Any]]:
        params = (await bridge.call("list_parameters"))["parameters"]
        return [p for p in params if p["user"]] if user_only else params

    @server.tool(description="Change a parameter's expression (for example '25 mm' or 'width * 2'). Fusion recomputes the model; returns the old and new values.", annotations=WRITE)
    async def set_parameter(
        name: str = Field(description="Exact parameter name, as returned by list_parameters"),
        expression: str = Field(description="New expression including units, e.g. '25 mm', '3 deg' or 'width / 2'"),
    ) -> dict[str, Any]:
        return await bridge.call("set_parameter", name=name, expression=expression)

    @server.tool(description="Start exporting the active design to STEP, STL or F3D. Returns a job id immediately; poll get_job until it completes.", annotations=WRITE)
    async def start_export(
        format: Literal["step", "stl", "f3d"] = Field(description="Output format"),
        path: str = Field(description="Destination file path on the machine running Fusion"),
    ) -> dict[str, Any]:
        job = Job(id=uuid.uuid4().hex[:12], kind=f"export:{format}")
        jobs[job.id] = job

        async def work():
            try:
                job.result = await bridge.call("export", format=format, path=path)
                job.status = "completed"
            except Exception as e:
                job.status, job.error = "failed", str(e)
            finally:
                job.finished = time.time()

        asyncio.get_running_loop().create_task(work())
        return job.view()

    @server.tool(description="Check the status of a job started by start_export.", annotations=READ)
    async def get_job(job_id: str = Field(description="The job_id returned by start_export")) -> dict[str, Any]:
        job = jobs.get(job_id)
        if not job:
            raise ValueError(f"Unknown job {job_id!r}")
        return job.view()

    return server


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(prog="fusion-mcp-server")
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    args = p.parse_args()
    create_server().run(transport=args.transport)


if __name__ == "__main__":
    main()
