# MCP Server - SAP BW Modeling API

An MCP (Model Context Protocol) server that exposes SAP BW4/HANA modeling objects as tools via [FastMCP](https://gofastmcp.com). Allows AI assistants to explore and query your BW system's metadata: InfoAreas, InfoObjects, ADSOs, CompositeProviders, Queries, and more.

## Prerequisites

- Python 3.10+
- Access to a SAP BW4/HANA system with the BW Modeling API enabled (`/sap/bw/modeling/`)
- A SAP user with appropriate authorizations for the Modeling API

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

The server is configured via environment variables:

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `BW_BASE_URL` | Yes | Base URL of the BW system (e.g. `https://bw4hana.example.com`) | — |
| `BW_USERNAME` | Yes | SAP username | — |
| `BW_PASSWORD` | Yes | SAP password | — |
| `BW_CLIENT` | No | SAP client number | `100` |
| `BW_VERIFY_SSL` | No | Verify SSL certificates (`true`/`false`) | `false` |

## Running the Server

### Standalone (stdio transport)

```bash
python server.py
```

### With FastMCP CLI

```bash
fastmcp run server.py:mcp
```

### HTTP transport

```bash
fastmcp run server.py:mcp --transport http --port 8000
```

## MCP Client Configuration

Add this to your MCP client config (e.g. `.kiro/settings/mcp.json`):

```json
{
  "mcpServers": {
    "bw-modeling": {
      "command": "python",
      "args": ["path/to/server.py"],
      "env": {
        "BW_BASE_URL": "https://your-bw-system.example.com",
        "BW_USERNAME": "your_user",
        "BW_PASSWORD": "your_password",
        "BW_CLIENT": "100"
      }
    }
  }
}
```

## Running from GitHub with uvx (no local clone)

Colleagues can run this server straight from the GitHub repo without cloning it,
using `uvx` (the runner that ships with [uv](https://docs.astral.sh/uv/)). This
is the Python equivalent of `npx` for Node packages.

### Prerequisite

Each user needs `uv` installed (it provides the `uvx` command). Install it once:

- Windows (PowerShell): `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

See the [uv install guide](https://docs.astral.sh/uv/getting-started/installation/)
for other options. No manual clone or `pip install` is needed — `uvx` fetches,
builds, and runs the server on demand.

### Client configuration

```json
{
  "mcpServers": {
    "bw-modeling": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/felipetomazzi-source/mcp-bw-modelling-api.git@v1.0.0",
        "mcp-bw-modeling-api"
      ],
      "env": {
        "BW_BASE_URL": "https://your-bw-system.example.com",
        "BW_USERNAME": "your_user",
        "BW_PASSWORD": "your_password",
        "BW_CLIENT": "100"
      }
    }
  }
}
```

### Version pinning (recommended for sharing)

The `@v1.0.0` suffix pins to a specific release tag. This matters for teams:

- With a pinned tag, each user's `uvx` cache stays valid across day-to-day
  pushes to `main`; it only rebuilds when you publish a new tag.
- Without a tag (tracking `main`), the cache is invalidated on every push, so
  users hit a slow cold rebuild (see below) after each change you make.

To ship an update: push to `main`, create a new tag (e.g. `v1.1.0`), and have
users bump the `@v1.0.0` in their config.

### First-launch behavior (important)

On the very first launch on a machine (or the first launch of a new version),
`uvx` clones the repo and installs its dependencies before the server starts.
This cold build can take longer than the MCP client's startup window, so the
server may show as failed on the first attempt and then connect on a retry.

Notes:
- Kiro's MCP configuration has no startup-timeout setting, so this cannot be
  extended via config. If a first launch times out, simply restart the server.
- To avoid the timeout entirely, pre-warm the cache once in a terminal before
  first use (this fetches + builds without any client timeout):

  ```bash
  uvx --from "git+https://github.com/felipetomazzi-source/mcp-bw-modelling-api.git@v1.0.0" mcp-bw-modeling-api
  ```

  Press Ctrl+C once it prints the "Starting MCP server ... with transport
  'stdio'" banner. Subsequent launches from the client are fast.

## Available Tools

| Tool | Description |
|------|-------------|
| `check_connection` | Test connectivity to the BW system |
| `search_bw_objects` | Search for any BW object by name/type |
| `list_infoareas` | List all InfoAreas (organizational folders) |
| `list_infoobjects` | List InfoObjects (characteristics, key figures) |
| `list_adsos` | List Advanced DataStore Objects |
| `list_composite_providers` | List CompositeProviders (HCPR) |
| `list_queries` | List BEx/BW Queries |
| `get_object_details` | Get full metadata for any BW object |
| `get_infoobject_details` | Get InfoObject properties, incl. navigational vs display attributes and compounding |
| `get_adso_fields` | Get field list of an ADSO |
| `get_composite_provider_parts` | Get the source (part) providers of a CompositeProvider |
| `get_infoarea_contents` | List objects in an InfoArea (flat search) |
| `get_infoarea_tree` | List objects under an InfoArea via the InfoProvider structure (recursive, reliable) |
| `get_transformation_details` | Get transformation metadata (source, target, rules) |
| `get_query_structure` | List a query's characteristics, key figures, and measures (restricted + calculated) |
| `get_query_filters` | Read a query's filters (fixed/default) and restricted key figures |
| `get_data_flow` | Show lineage: inbound/outbound transformations and DTPs (with source/target, status, and process chains) |

## Supported Object Types

| Code | Object Type |
|------|-------------|
| `AREA` | InfoArea |
| `IOBJ` | InfoObject |
| `ADSO` | DataStore Object (advanced) |
| `HCPR` | CompositeProvider |
| `QUERY` | Query |
| `TRFN` | Transformation |
| `DTP` | Data Transfer Process |
| `MPRO` | MultiProvider |
| `ODSO` | DataStore Object (classic) |
