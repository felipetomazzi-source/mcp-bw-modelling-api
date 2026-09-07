# MCP Server - SAP BW Modeling API

An MCP (Model Context Protocol) server that exposes SAP BW4/HANA modeling objects as tools via [FastMCP](https://gofastmcp.com). Allows AI assistants to explore and query your BW system's metadata: InfoAreas, InfoObjects, ADSOs, CompositeProviders, Queries, and more.

## Compatibility

This server has only been tested against **SAP BW/4HANA**, and it relies on the
BW Modeling API (the `/sap/bw/modeling/` ADT-based endpoints).

It does **not** work on classic **BW 7.5 on AnyDB** (non-HANA). Testing on a
BW 7.5 AnyDB system showed the `/sap/bw/modeling/` endpoints are not available
there, so the tools fail to retrieve anything. The BW Modeling API is a
BW/4HANA (and BW-on-HANA modeling) capability and is not exposed on classic
AnyDB installations. Use this server only against BW/4HANA.

## Prerequisites

- Python 3.10+
- A SAP **BW/4HANA** system with the BW Modeling API enabled (`/sap/bw/modeling/`).
  Classic BW 7.5 on AnyDB is not supported (see [Compatibility](#compatibility)).
- A SAP user with appropriate authorizations for the Modeling API

## Installation

Using a **virtual environment** is strongly recommended. It isolates this
server's dependencies (notably `fastmcp` and its pinned `pydantic`) from your
system-wide Python, so a later global package upgrade can't silently break the
server. Mismatched `fastmcp`/`pydantic` versions are a common cause of the
server failing to start.

Create and activate a venv, then install the dependencies into it:

```bash
# Create the virtual environment (once)
python -m venv .venv

# Activate it
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Install dependencies into the venv
pip install -r requirements.txt
```

When configuring the MCP client, point `command` at the venv's Python
interpreter so the server always runs with the isolated dependencies — for
example `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on
macOS/Linux (see [MCP Client Configuration](#mcp-client-configuration)).

Without a venv, install globally instead:

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

Add this to your MCP client config (e.g. `.kiro/settings/mcp.json`). If you set
up a virtual environment (recommended), set `command` to the venv's Python
interpreter rather than a bare `python`, so the server runs with the isolated
dependencies:

```json
{
  "mcpServers": {
    "bw-modeling": {
      "command": "path/to/.venv/Scripts/python.exe",
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
