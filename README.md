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
| `get_characteristic_values` | Read the actual master-data values of a characteristic (e.g. list 0CUSTOMER/0MATERIAL values), optionally restricted to an InfoProvider |
| `get_adso_fields` | Get field list of an ADSO |
| `get_composite_provider_parts` | Get the source (part) providers of a CompositeProvider |
| `get_infoarea_contents` | List objects in an InfoArea (flat search) |
| `get_infoarea_tree` | List objects under an InfoArea via the InfoProvider structure (recursive, reliable) |
| `get_transformation_details` | Get transformation metadata (source, target, rules) |
| `get_dtp_details` | Get a Data Transfer Process's source/target, settings, and (uniquely) its filter restrictions |
| `get_query_structure` | List a query's characteristics, key figures, and measures (restricted + calculated) |
| `get_query_filters` | Read a query's filters (fixed/default) and restricted key figures |
| `read_query_data` | Execute a query and return its result data (default view only) - see [Reading Query Result Data](#reading-query-result-data) |
| `read_query_data_drilldown` | Execute a query with an ad-hoc drilldown and/or a mandatory-variable screen submitted automatically - see [Reading Query Result Data](#reading-query-result-data) |
| `get_data_flow` | Show lineage: inbound/outbound transformations and DTPs (with source/target, status, and process chains) |

## Reading Query Result Data

Two tools execute a query and return its actual figures, not just its
design. They trade off reliability against capability, so try
`read_query_data` first and only reach for `read_query_data_drilldown` when
you hit one of its limitations:

- **`read_query_data`** runs the query via the BW reporting (BICS) runtime -
  a single, stable API call. It returns exactly the query's saved default
  view: default variable values, default drilldown. If the query has a
  *mandatory* variable, this call returns no data (just the variable list) -
  it cannot submit one.
- **`read_query_data_drilldown`** exists to cover that gap and to add an
  ad-hoc characteristic to the drilldown that the query's default view
  doesn't show. It does so via **InA (Information Access)**, the internal
  HTTP API behind BW/4HANA's own "Data Preview" Fiori app
  (`/sap/bw/ina/GetResponse`) - reverse-engineered from that app's
  client-side source, since SAP does not publicly document it. It performs
  a 3-call handshake (fetch metadata, submit variables at their default
  value, request the result grid) and echoes back the query's design-time
  filters so the server doesn't aggregate more than the query needs.

Because it rides on an undocumented, unversioned protocol rather than the
BW Modeling API, `read_query_data_drilldown` is inherently less
future-proof: SAP could change `/sap/bw/ina/*` without notice. Use it when
you need what it uniquely offers, but don't treat it as a drop-in
replacement for `read_query_data`.

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
