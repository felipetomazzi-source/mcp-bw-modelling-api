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
| `get_infoobject_details` | Get InfoObject properties |
| `get_adso_fields` | Get field list of an ADSO |
| `get_composite_provider_parts` | Get data sources of a CompositeProvider |
| `get_infoarea_contents` | List all objects in an InfoArea |

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
