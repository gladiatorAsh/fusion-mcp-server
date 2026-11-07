# fusion-mcp-server

<!-- mcp-name: io.github.gladiatorAsh/fusion-mcp-server -->

An MCP server for **Autodesk Fusion**. Ask an agent to read a design, change its dimensions, or export it, from Claude, VS Code, Copilot or any other MCP client.

> **Status: early.** The MCP server, the job model for long exports and the Fusion add-in are written. The server is tested against a fake bridge, and the add-in still needs hands-on testing inside Fusion. See the [roadmap](#roadmap).
>
> This is an independent, personal project built from scratch on the public Fusion API. It isn't affiliated with Autodesk.

## How it works

```
MCP client ──stdio / Streamable HTTP──▶ fusion-mcp-server ──HTTP + token──▶ FusionMCPBridge add-in ──▶ Fusion API
 (Claude, VS Code…)                      (this package)        127.0.0.1         (inside Fusion)            (main thread)
```

Fusion's API only runs inside Fusion and isn't thread-safe, so the system has two parts:

1. **FusionMCPBridge** (`addin/`) is a Fusion add-in that serves a small JSON API on `127.0.0.1`. Each request is handed to Fusion's main thread through a custom event. A fresh token is created every session and stored in `~/.fusion-mcp/token`, readable only by you, so other local programs can't drive Fusion.
2. **fusion-mcp-server** (`src/`) is the MCP server, built on the official Python SDK 2.x. It exposes typed, annotated tools and calls the bridge.

### Tools

| Tool | What it does | Annotations |
| --- | --- | --- |
| `get_design_info` | Document name, units, component, body, sketch and feature counts | read-only |
| `list_parameters` | Every parameter with its expression, value and unit | read-only |
| `set_parameter` | Change an expression (`'25 mm'`, `'width * 2'`); Fusion recomputes the model | write, idempotent |
| `start_export` | Export to STEP, STL or F3D as a background job | write |
| `get_job` | Poll a job until it completes or fails | read-only |

**Why jobs?** Exporting a large assembly can take minutes. `start_export` returns at once and `get_job` reports progress, so no request stays open for minutes. That fits the stateless 2026-07-28 protocol, and the pattern maps directly onto the MCP **Tasks** extension, which this server will move to once the Python SDK supports it.

## Setup

1. **Install the add-in.** In Fusion, open *Utilities → Add-Ins → Scripts and Add-Ins*, click **+**, choose `addin/FusionMCPBridge`, and run it.
2. **Install the server:**
   ```bash
   pip install fusion-mcp-server        # or, from a clone: pip install -e .
   ```
3. **Add it to your MCP client** (`uvx` runs it without a manual install; `"command": "fusion-mcp-server"` works too after `pip install`):
   ```json
   {
     "mcpServers": {
       "fusion": { "command": "uvx", "args": ["fusion-mcp-server"] }
     }
   }
   ```
4. Ask: *"What user parameters does this design have? Make the bracket 25 mm wide and export it as STEP to ~/Desktop/bracket.step."*

## Development

```bash
pip install -e ".[dev]"
pytest -q                                              # tools tested through a real MCP client, fake bridge
mcp-tool-bench lint "python -m fusion_mcp"             # 0 errors, 0 warnings
```

## Roadmap

- [x] MCP server with typed, annotated tools (Python SDK 2.x, stdio and Streamable HTTP)
- [x] Background jobs for exports
- [x] Fusion add-in: token-protected localhost bridge with main-thread dispatch
- [x] Tests through an in-process MCP client; clean `mcp-tool-bench lint`
- [x] Release workflow: tag to publish on PyPI and the official MCP Registry
- [ ] Test the add-in end to end in Fusion on Windows and macOS
- [ ] Move long-running work to the MCP Tasks extension
- [ ] More tools: sketch and extrude from a description, timeline inspection, mass properties, screenshots of the viewport
- [ ] Return exported parts as resources that [mcp-cad-viewer](https://github.com/gladiatorAsh/mcp-cad-viewer) can render inline
- [ ] Tool-selection evals with [mcp-tool-bench](https://github.com/gladiatorAsh/mcp-tool-bench) across several models
- [ ] Confirmation step (elicitation) before changes that affect many features

## License

MIT © Ashutosh Singh
