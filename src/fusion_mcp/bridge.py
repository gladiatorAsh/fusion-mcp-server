"""HTTP client for the FusionMCPBridge add-in running inside Fusion."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


class BridgeError(RuntimeError):
    pass


class FusionBridge:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 330.0):
        self.base_url = base_url or os.environ.get("FUSION_MCP_URL", "http://127.0.0.1:47120")
        self._token = token
        self.timeout = timeout

    @property
    def token(self) -> str:
        if self._token:
            return self._token
        path = Path(os.environ.get("FUSION_MCP_TOKEN_FILE", Path.home() / ".fusion-mcp" / "token"))
        try:
            return path.read_text().strip()
        except FileNotFoundError:
            raise BridgeError("Fusion isn't reachable: start Fusion and run the FusionMCPBridge add-in") from None

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/call",
                    json={"method": method, "params": params},
                    headers={"Authorization": f"Bearer {self.token}"},
                )
        except httpx.ConnectError:
            raise BridgeError("Fusion isn't reachable: start Fusion and run the FusionMCPBridge add-in") from None
        body = r.json()
        if not body.get("ok"):
            raise BridgeError(body.get("error", f"bridge returned HTTP {r.status_code}"))
        return body["result"]
