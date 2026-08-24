"""
MCP Server for SAP BW Modeling API

Exposes SAP BW4/HANA modeling resources (InfoAreas, InfoObjects, ADSOs,
CompositeProviders, Queries, etc.) as MCP tools via FastMCP.
"""

import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests
import urllib3
from fastmcp import FastMCP

# Suppress InsecureRequestWarning for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BW_BASE_URL = os.environ.get("BW_BASE_URL", "")
BW_USERNAME = os.environ.get("BW_USERNAME", "")
BW_PASSWORD = os.environ.get("BW_PASSWORD", "")
BW_CLIENT = os.environ.get("BW_CLIENT", "100")
BW_VERIFY_SSL = os.environ.get("BW_VERIFY_SSL", "false").lower() == "true"

# XML namespaces used in BW Modeling API responses
NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
    "bwModel": "http://www.sap.com/bw/modeling",
}

# BW Modeling API object types
OBJECT_TYPES = {
    "AREA": "InfoArea",
    "IOBJ": "InfoObject",
    "ADSO": "DataStore Object (advanced)",
    "HCPR": "CompositeProvider",
    "QUERY": "Query",
    "TRFN": "Transformation",
    "DTP": "Data Transfer Process",
    "MPRO": "MultiProvider",
    "ODSO": "DataStore Object (classic)",
}

# ---------------------------------------------------------------------------
# BW API Client
# ---------------------------------------------------------------------------


@dataclass
class BWConnection:
    """Holds connection parameters for a BW system."""

    base_url: str
    username: str
    password: str
    client: str
    verify_ssl: bool

    @classmethod
    def from_env(cls) -> "BWConnection":
        """Create a connection from environment variables."""
        if not BW_BASE_URL:
            raise ValueError(
                "BW_BASE_URL environment variable is required. "
                "Set it to your BW system URL (e.g. https://bw4hana.example.com)"
            )
        if not BW_USERNAME or not BW_PASSWORD:
            raise ValueError(
                "BW_USERNAME and BW_PASSWORD environment variables are required."
            )
        return cls(
            base_url=BW_BASE_URL.rstrip("/"),
            username=BW_USERNAME,
            password=BW_PASSWORD,
            client=BW_CLIENT,
            verify_ssl=BW_VERIFY_SSL,
        )


def _bw_request(
    conn: BWConnection,
    path: str,
    params: dict | None = None,
    accept: str = "application/xml",
) -> requests.Response:
    """Make an authenticated GET request to the BW Modeling API."""
    url = f"{conn.base_url}{path}"
    headers = {
        "Accept": accept,
        "sap-client": conn.client,
    }
    response = requests.get(
        url,
        auth=(conn.username, conn.password),
        headers=headers,
        params=params,
        verify=conn.verify_ssl,
        timeout=60,
    )
    response.raise_for_status()
    return response


def _parse_search_results(xml_text: str) -> list[dict]:
    """Parse BW search API XML response into a list of objects."""
    root = ET.fromstring(xml_text)
    results = []
    entries = root.findall("atom:entry", NAMESPACES)

    for entry in entries:
        obj_elem = entry.find("atom:content/bwModel:object", NAMESPACES)
        title_elem = entry.find("atom:title", NAMESPACES)

        if obj_elem is not None:
            result = {
                "name": obj_elem.get("objectName", ""),
                "type": obj_elem.get("objectType", ""),
                "description": title_elem.text if title_elem is not None else "",
                "infoArea": obj_elem.get("infoArea", ""),
            }
            results.append(result)

    return results


def _parse_object_details(xml_text: str) -> dict:
    """Parse BW object detail XML response."""
    root = ET.fromstring(xml_text)

    # Try to extract properties from Atom entry format
    entry = root.find("atom:entry", NAMESPACES) or root
    content = entry.find("atom:content", NAMESPACES) if entry is not None else None

    details = {}

    # Extract title
    title_elem = entry.find("atom:title", NAMESPACES) if entry is not None else None
    if title_elem is not None and title_elem.text:
        details["description"] = title_elem.text

    # Extract properties from d:properties or bwModel namespace
    if content is not None:
        props = content.find("m:properties", NAMESPACES)
        if props is not None:
            for child in props:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child.text:
                    details[tag] = child.text

    # Also try bwModel:object attributes
    obj_elem = (
        content.find("bwModel:object", NAMESPACES) if content is not None else None
    )
    if obj_elem is not None:
        for key, value in obj_elem.attrib.items():
            details[key] = value

    return details


def _parse_hcpr_details(xml_text: str) -> dict:
    """Parse key metadata from a Composite:compositeView (CompositeProvider).

    The CompositeProvider model is not Atom-based; its descriptive metadata
    lives in a <tlogoProperties> child of the root, and the display label in
    <endUserTexts>. Also summarize the source providers and combination type.
    """
    root = ET.fromstring(xml_text)

    def _local(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    def _xsi(elem) -> str:
        v = elem.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
        return v.split(":")[-1] if ":" in v else v

    details: dict = {}

    # Root attributes (name, schema/model flags)
    details["name"] = root.get("name", "")
    if root.get("withHanaModel"):
        details["withHanaModel"] = root.get("withHanaModel")

    for child in list(root):
        tag = _local(child.tag)
        if tag == "endUserTexts" and child.get("label"):
            details["description"] = child.get("label")
        elif tag == "tlogoProperties":
            for key in (
                "description", "responsible", "createdBy", "createdAt",
                "changedBy", "changedAt", "version", "masterLanguage",
            ):
                if child.get(key):
                    details.setdefault(key, child.get(key))

    # Summarize source (part) providers and their combination type.
    part_providers = []
    combination = ""
    for node in root.iter():
        if _local(node.tag) != "viewNode":
            continue
        combination = combination or _xsi(node)
        for inp in list(node):
            if _local(inp.tag) == "input" and inp.get("name"):
                part_providers.append(inp.get("name"))
    if part_providers:
        details["combination"] = combination
        details["partProviders"] = part_providers

    return details


def _parse_value_help(xml_text: str) -> list[dict]:
    """Parse a BW Modeling value-help response into a list of row dicts.

    The value-help format uses the http://www.sap.com/bw/modeling namespace and
    consists of a <valueHelpCatalog> defining columns (in order) and
    <valueHelpValues> containing rows of <value> elements in the same order.
    """
    root = ET.fromstring(xml_text)
    ns = {"vh": "http://www.sap.com/bw/modeling"}

    # Column names, in order, from the catalog
    columns = [
        (c.findtext("vh:columnname", default="", namespaces=ns) or "").strip()
        for c in root.findall(".//vh:valueHelpCatalog/vh:column", ns)
    ]

    rows = []
    for row in root.findall(".//vh:valueHelpValues/vh:row", ns):
        values = [(v.text or "").strip() for v in row.findall("vh:value", ns)]
        record = {}
        for idx, col in enumerate(columns):
            record[col] = values[idx] if idx < len(values) else ""
        rows.append(record)

    return rows


# ---------------------------------------------------------------------------
# FastMCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "SAP BW Modeling API",
    instructions=(
        "Access SAP BW/4HANA modeling objects (InfoAreas, InfoObjects, ADSOs, "
        "CompositeProviders, Queries, Transformations) via the BW Modeling API.\n"
        "\n"
        "OBJECT TYPE CODES (used by search_bw_objects / get_object_details):\n"
        "  AREA=InfoArea, IOBJ=InfoObject, ADSO=DataStore Object (advanced),\n"
        "  HCPR=CompositeProvider, QUERY=Query, TRFN=Transformation,\n"
        "  DTP=Data Transfer Process, MPRO=MultiProvider, ODSO=classic DSO.\n"
        "\n"
        "TYPICAL WORKFLOW - explore, then drill into details:\n"
        "  1. Discover objects: search_bw_objects (any type) or the typed\n"
        "     listers list_infoareas / list_infoobjects / list_adsos /\n"
        "     list_composite_providers / list_queries. Wildcards use trailing\n"
        "     '*' (e.g. 'B080*'); leading wildcards are not supported.\n"
        "  2. Inspect an object with the matching detail tool:\n"
        "     - InfoObject   -> get_infoobject_details (attributes, nav vs\n"
        "                       display, compounding, data type)\n"
        "     - ADSO         -> get_adso_fields (field/InfoObject list)\n"
        "     - HCPR         -> get_composite_provider_parts (source providers)\n"
        "     - Query        -> get_query_structure (characteristics, key\n"
        "                       figures, measures) AND get_query_filters\n"
        "                       (fixed/default filters, restricted key figures)\n"
        "     - Transformation -> get_transformation_details\n"
        "     - anything     -> get_object_details (generic metadata)\n"
        "\n"
        "FINDING QUERIES ON A PROVIDER: call list_queries with info_provider=\n"
        "  '<HCPR or ADSO name>' (e.g. 'B080_V05'). Do not use info_area for\n"
        "  queries. For a full picture of one query, combine get_query_structure\n"
        "  with get_query_filters.\n"
        "\n"
        "Use check_connection first if calls fail, to confirm connectivity/auth."
    ),
)


@mcp.tool
def search_bw_objects(
    search_term: str = "*",
    object_type: str = "",
    max_results: int = 100,
) -> list[dict]:
    """
    Search for BW modeling objects by name or description.

    Args:
        search_term: Search pattern (supports * wildcard). Default "*" returns all.
        object_type: Filter by object type. Valid values:
            AREA (InfoArea), IOBJ (InfoObject), ADSO (DataStore Object),
            HCPR (CompositeProvider), QUERY (Query), TRFN (Transformation),
            DTP (Data Transfer Process), MPRO (MultiProvider), ODSO (classic DSO).
            Leave empty to search all types.
        max_results: Maximum number of results to return (default: 100).

    Returns:
        List of matching objects with name, type, description, and infoArea.
    """
    conn = BWConnection.from_env()

    params = {
        "searchTerm": search_term,
        "maxSize": str(max_results),
        "searchInName": "true",
    }
    if object_type:
        params["objectType"] = object_type.upper()

    response = _bw_request(
        conn, "/sap/bw/modeling/repo/is/bwsearch", params=params
    )
    results = _parse_search_results(response.text)

    # Add human-readable type names
    for r in results:
        r["typeName"] = OBJECT_TYPES.get(r["type"], r["type"])

    return results


@mcp.tool
def list_infoareas(max_results: int = 500) -> list[dict]:
    """
    List all InfoAreas in the BW system.

    InfoAreas are organizational folders used to group BW objects.

    Args:
        max_results: Maximum number of results (default: 500).

    Returns:
        List of InfoAreas with name and description.
    """
    return search_bw_objects(search_term="*", object_type="AREA", max_results=max_results)


@mcp.tool
def list_infoobjects(
    search_term: str = "*",
    info_area: str = "",
    max_results: int = 200,
) -> list[dict]:
    """
    List InfoObjects in the BW system.

    InfoObjects are the smallest units of information in BW (characteristics,
    key figures, time characteristics, units).

    Args:
        search_term: Filter by name pattern (supports * wildcard).
        info_area: Filter by InfoArea technical name. Leave empty for all.
        max_results: Maximum number of results (default: 200).

    Returns:
        List of InfoObjects with name, description, and infoArea.
    """
    conn = BWConnection.from_env()

    params = {
        "searchTerm": search_term,
        "maxSize": str(max_results),
        "objectType": "IOBJ",
        "searchInName": "true",
    }
    if info_area:
        params["infoArea"] = info_area

    response = _bw_request(
        conn, "/sap/bw/modeling/repo/is/bwsearch", params=params
    )
    return _parse_search_results(response.text)


@mcp.tool
def list_adsos(
    search_term: str = "*",
    info_area: str = "",
    max_results: int = 200,
) -> list[dict]:
    """
    List Advanced DataStore Objects (ADSOs) in the BW system.

    ADSOs are the primary data storage objects in BW4/HANA.

    Args:
        search_term: Filter by name pattern (supports * wildcard).
        info_area: Filter by InfoArea technical name. Leave empty for all.
        max_results: Maximum number of results (default: 200).

    Returns:
        List of ADSOs with name, description, and infoArea.
    """
    conn = BWConnection.from_env()

    params = {
        "searchTerm": search_term,
        "maxSize": str(max_results),
        "objectType": "ADSO",
        "searchInName": "true",
    }
    if info_area:
        params["infoArea"] = info_area

    response = _bw_request(
        conn, "/sap/bw/modeling/repo/is/bwsearch", params=params
    )
    return _parse_search_results(response.text)


@mcp.tool
def list_composite_providers(
    search_term: str = "*",
    info_area: str = "",
    max_results: int = 200,
) -> list[dict]:
    """
    List CompositeProviders (HCPRs) in the BW system.

    CompositeProviders combine multiple data sources (ADSOs, InfoObjects,
    Open ODS Views) for reporting via union or join.

    Args:
        search_term: Filter by name pattern (supports * wildcard).
        info_area: Filter by InfoArea technical name. Leave empty for all.
        max_results: Maximum number of results (default: 200).

    Returns:
        List of CompositeProviders with name, description, and infoArea.
    """
    conn = BWConnection.from_env()

    params = {
        "searchTerm": search_term,
        "maxSize": str(max_results),
        "objectType": "HCPR",
        "searchInName": "true",
    }
    if info_area:
        params["infoArea"] = info_area

    response = _bw_request(
        conn, "/sap/bw/modeling/repo/is/bwsearch", params=params
    )
    return _parse_search_results(response.text)


@mcp.tool
def list_queries(
    search_term: str = "*",
    info_area: str = "",
    info_provider: str = "",
    max_results: int = 200,
) -> list[dict]:
    """
    List BEx/BW Queries in the system.

    Queries are reporting definitions built on top of CompositeProviders or ADSOs.

    RECOMMENDED USAGE: to list the queries built on a specific InfoProvider
    (CompositeProvider or ADSO), pass info_provider (e.g. "B080_V05"). This uses
    a dedicated endpoint and is the reliable way to answer "which queries run on
    provider X". Do NOT use info_area for that - queries belong to an
    InfoProvider, not an InfoArea.

    KNOWN LIMITATIONS (of the generic name-search path, used when info_provider
    is empty):
      - info_area filtering with objectType=QUERY tends to fail with HTTP 500
        on many systems; prefer info_provider instead.
      - Leading-wildcard patterns (e.g. "*B080*") are rejected by the search
        API and fail. Use a prefix pattern ("B080*") or an exact name.

    Args:
        search_term: Filter by name pattern. Trailing "*" wildcard works
            ("B080*"); a leading wildcard ("*B080*") is not supported.
        info_area: Filter by InfoArea technical name. Leave empty for all.
            Not recommended for queries (see KNOWN LIMITATIONS).
        info_provider: Filter by InfoProvider (e.g. CompositeProvider or ADSO,
            such as "B080_V05"). This is the recommended way to list the queries
            built on a specific provider. Leave empty to search all.
        max_results: Maximum number of results (default: 200).

    Returns:
        List of Queries. When info_provider is given, each entry has the query
        technical name, description, InfoProvider, and last-changed metadata.
        Otherwise, entries have name, type, description, and infoArea.
    """
    conn = BWConnection.from_env()

    # When filtering by InfoProvider, use the dedicated queries value-help
    # endpoint. Queries belong to an InfoProvider (not an InfoArea), and the
    # generic bwsearch endpoint does not support this filter.
    if info_provider:
        params = {
            "infoprovider": info_provider,
            "maxrows": str(max_results),
        }
        if search_term and search_term != "*":
            params["searchstring"] = search_term
            params["searchinname"] = "true"
            params["searchindescription"] = "true"

        response = _bw_request(
            conn,
            "/sap/bw/modeling/is/values/queries",
            params=params,
            accept="application/vnd.sap-bw-modeling.valuehelp2-v1_1_0+xml",
        )
        rows = _parse_value_help(response.text)

        queries = []
        for row in rows:
            queries.append(
                {
                    "name": row.get("COMPID", ""),
                    "description": row.get("TXTLG", "") or row.get("TXTSH", ""),
                    "infoProvider": row.get("INFOCUBE", ""),
                    "compUid": row.get("COMPUID", ""),
                    "lastChangedBy": row.get("TSTPNM", ""),
                    "changedOn": row.get("TSTPDAT", ""),
                    "type": "QUERY",
                }
            )
        return queries

    params = {
        "searchTerm": search_term,
        "maxSize": str(max_results),
        "objectType": "QUERY",
        "searchInName": "true",
    }
    if info_area:
        params["infoArea"] = info_area

    response = _bw_request(
        conn, "/sap/bw/modeling/repo/is/bwsearch", params=params
    )
    return _parse_search_results(response.text)


@mcp.tool
def get_object_details(
    object_name: str,
    object_type: str,
) -> dict:
    """
    Get detailed metadata for a specific BW object.

    Args:
        object_name: Technical name of the object (e.g. "0MATERIAL", "ZSDADSO01").
        object_type: Object type code: IOBJ, ADSO, HCPR, QUERY, TRFN, DTP, AREA.

    Returns:
        Dictionary with object metadata and properties.
    """
    conn = BWConnection.from_env()

    object_type = object_type.upper()

    # Map object types to their API paths (from BW Modeling API discovery)
    type_paths = {
        "IOBJ": f"/sap/bw/modeling/iobj/{object_name}",
        "ADSO": f"/sap/bw/modeling/adso/{object_name}",
        "HCPR": f"/sap/bw/modeling/hcpr/{object_name}",
        "QUERY": f"/sap/bw/modeling/query/{object_name}/A",
        "TRFN": f"/sap/bw/modeling/trfn/{object_name}",
        "DTPA": f"/sap/bw/modeling/dtpa/{object_name}",
        "DTP": f"/sap/bw/modeling/dtpa/{object_name}",
        "AREA": f"/sap/bw/modeling/area/{object_name}",
        "MPRO": f"/sap/bw/modeling/mpro/{object_name}",
        "ODSO": f"/sap/bw/modeling/odso/{object_name}",
        "DEST": f"/sap/bw/modeling/dest/{object_name}",
    }

    # Content types expected by the BW Modeling API per object type
    type_accepts = {
        "IOBJ": "application/vnd.sap-bw-modeling.iobj-v2_1_0+xml",
        "ADSO": "application/vnd.sap.bw.modeling.adso-v1_5_0+xml",
        "HCPR": "application/vnd.sap.bw.modeling.hcpr-v1_15_0+xml",
        "QUERY": "application/vnd.sap.bw.modeling.query-v1_10_0+xml",
        "TRFN": "application/vnd.sap.bw.modeling.trfn-v1_0_0+xml",
        "DTPA": "application/vnd.sap.bw.modeling.dtpa-v1_0_0+xml",
        "DTP": "application/vnd.sap.bw.modeling.dtpa-v1_0_0+xml",
        "DEST": "application/vnd.sap.bw.modeling.dest-v1_0_0+xml",
    }

    path = type_paths.get(object_type)
    if not path:
        return {"error": f"Unsupported object type: {object_type}. Use one of: {list(type_paths.keys())}"}

    accept = type_accepts.get(object_type, "application/xml")

    try:
        response = _bw_request(conn, path, accept=accept)
        if object_type == "QUERY":
            # Query uses the Qry:queryResource schema, not the Atom/properties
            # format; extract key attributes from the nested Query element.
            details = _parse_query_details(response.text)
        elif object_type == "HCPR":
            # CompositeProvider uses the Composite:compositeView schema; the
            # metadata lives in the tlogoProperties child element.
            details = _parse_hcpr_details(response.text)
        else:
            details = _parse_object_details(response.text)
        details["objectName"] = object_name
        details["objectType"] = object_type
        details["typeName"] = OBJECT_TYPES.get(object_type, object_type)
        return details
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.reason}", "object": object_name}


@mcp.tool
def get_adso_fields(object_name: str) -> list[dict]:
    """
    Get the field list (InfoObjects) of an Advanced DataStore Object.

    Args:
        object_name: Technical name of the ADSO (e.g. "ZSDADSO01").

    Returns:
        List of fields with name, type (key/data), and associated InfoObject.
    """
    conn = BWConnection.from_env()

    path = f"/sap/bw/modeling/adso/{object_name}"

    try:
        response = _bw_request(
            conn, path, accept="application/vnd.sap.bw.modeling.adso-v1_5_0+xml"
        )
        root = ET.fromstring(response.text)
        fields = []
        entries = root.findall("atom:entry", NAMESPACES)

        for entry in entries:
            title = entry.find("atom:title", NAMESPACES)
            content = entry.find("atom:content", NAMESPACES)

            field = {
                "name": title.text if title is not None else "",
            }

            # Try to extract field properties
            if content is not None:
                props = content.find("m:properties", NAMESPACES)
                if props is not None:
                    for child in props:
                        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child.text:
                            field[tag] = child.text

            fields.append(field)

        return fields
    except requests.exceptions.HTTPError as e:
        return [{"error": f"HTTP {e.response.status_code}: {e.response.reason}", "object": object_name}]


@mcp.tool
def get_composite_provider_parts(object_name: str) -> list[dict]:
    """
    Get the part providers (data sources) of a CompositeProvider.

    A CompositeProvider combines several source InfoProviders (ADSOs,
    InfoObjects, Open ODS Views) via Union or Join nodes. This returns each
    source (part) provider with the combination type of its parent node.

    Args:
        object_name: Technical name of the CompositeProvider (e.g. "B080_V05").

    Returns:
        List of part providers, each with:
          - name: source provider technical name (e.g. an ADSO)
          - alias: the CompositeProvider's internal alias for the source
          - combination: "Union" or "Join" (from the parent view node)
    """
    conn = BWConnection.from_env()

    path = f"/sap/bw/modeling/hcpr/{object_name}"

    try:
        response = _bw_request(
            conn, path, accept="application/vnd.sap.bw.modeling.hcpr-v1_15_0+xml"
        )
    except requests.exceptions.HTTPError as e:
        return [{"error": f"HTTP {e.response.status_code}: {e.response.reason}", "object": object_name}]

    root = ET.fromstring(response.text)

    def _local(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    def _xsi(elem) -> str:
        v = elem.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
        return v.split(":")[-1] if ":" in v else v

    # The CompositeProvider model (Composite:compositeView) nests source
    # providers as <input> elements (xsi:type Composite:CompositeInput) inside
    # viewNode elements whose xsi:type is View:Union or View:Join.
    parts = []
    for node in root.iter():
        if _local(node.tag) != "viewNode":
            continue
        combination = _xsi(node)  # e.g. "Union" -> from "View:Union"
        for child in list(node):
            if _local(child.tag) != "input":
                continue
            parts.append(
                {
                    "name": child.get("name", ""),
                    "alias": child.get("alias", ""),
                    "combination": combination,
                }
            )

    return parts


@mcp.tool
def get_infoobject_details(infoobject_name: str) -> dict:
    """
    Get detailed properties of an InfoObject (characteristic or key figure).

    Returns the InfoObject's general properties, data-dictionary type info,
    compounding, text/master-data flags, and its attributes split into
    navigational (usable for query drilldown) versus display-only.

    Args:
        infoobject_name: Technical name (e.g. "0MATERIAL", "0PLANT", "0COSTCENTER").

    Returns:
        Dictionary with:
          - infoObjectType, infoArea, description, dataType/length/conversionExit
          - compounds: list of compounding InfoObjects (superior keys)
          - navigationAttributes: attributes usable as navigation attributes
          - displayAttributes: attributes shown only as display attributes
          - attributes: full attribute list with navigational/displayInQuery flags
    """
    conn = BWConnection.from_env()
    path = f"/sap/bw/modeling/iobj/{infoobject_name}"

    try:
        response = _bw_request(
            conn,
            path,
            accept="application/vnd.sap.bw.modeling.infoobject-v2_1_0+json",
        )
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "object": infoobject_name,
        }

    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        return {"error": "Could not parse InfoObject response", "object": infoobject_name}

    result: dict = {
        "objectName": infoobject_name,
        "objectType": "IOBJ",
        "infoObjectType": data.get("infoObjectType", ""),
    }

    general = data.get("generalProperties", {})
    result["infoArea"] = general.get("infoArea", "")
    descriptions = general.get("descriptions", [])
    if descriptions:
        first = descriptions[0]
        result["description"] = first.get("longText") or first.get("shortText", "")

    # Data-dictionary properties live under referenceOrBasisCharacteristic.
    basis = data.get("referenceOrBasisCharacteristic", {})
    ddic = basis.get("dataDictionary", {})
    if ddic:
        result["dataType"] = ddic.get("type", "")
        result["internalLength"] = ddic.get("internalLength")
        result["outputLength"] = ddic.get("outputLength")
        result["conversionExit"] = ddic.get("conversionExit", "")
        result["supportsLowercase"] = ddic.get("supportsLowercaseCharacters")
        result["highCardinality"] = ddic.get("hasHighCardinality")
    result["masterDataIsTimeDependent"] = basis.get("masterDataIsTimeDependant")

    # Compounding (superior key InfoObjects).
    result["compounds"] = data.get("compounds", []) or []

    # Attributes: split into navigational vs display-only. The
    # canBeUsedAsNavigationAttribute flag distinguishes them; noDisplayInQuery
    # marks an attribute hidden from query display.
    attributes = basis.get("attributes", []) or data.get("attributes", []) or []
    nav, disp, full = [], [], []
    for attr in attributes:
        name = attr.get("name", "")
        is_nav = bool(attr.get("canBeUsedAsNavigationAttribute", False))
        display_in_query = not bool(attr.get("noDisplayInQuery", False))
        full.append(
            {
                "name": name,
                "navigational": is_nav,
                "displayInQuery": display_in_query,
            }
        )
        if is_nav:
            nav.append(name)
        else:
            disp.append(name)

    result["navigationAttributes"] = nav
    result["displayAttributes"] = disp
    result["attributeCount"] = len(full)
    result["attributes"] = full

    return result


@mcp.tool
def get_infoarea_contents(
    info_area: str,
    max_results: int = 200,
) -> list[dict]:
    """
    List all objects within a specific InfoArea.

    Args:
        info_area: Technical name of the InfoArea.
        max_results: Maximum number of results (default: 200).

    Returns:
        List of all objects in the InfoArea with name, type, and description.
    """
    conn = BWConnection.from_env()

    params = {
        "searchTerm": "*",
        "maxSize": str(max_results),
        "searchInName": "true",
        "infoArea": info_area,
    }

    response = _bw_request(
        conn, "/sap/bw/modeling/repo/is/bwsearch", params=params
    )
    results = _parse_search_results(response.text)

    for r in results:
        r["typeName"] = OBJECT_TYPES.get(r["type"], r["type"])

    return results


@mcp.tool
def check_connection() -> dict:
    """
    Test the connection to the BW system.

    Returns:
        Connection status with system info or error details.
    """
    try:
        conn = BWConnection.from_env()
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    try:
        # Try a minimal search to verify connectivity
        params = {"searchTerm": "*", "maxSize": "1", "objectType": "AREA", "searchInName": "true"}
        response = _bw_request(conn, "/sap/bw/modeling/repo/is/bwsearch", params=params)
        return {
            "status": "connected",
            "system": conn.base_url,
            "client": conn.client,
            "user": conn.username,
            "http_status": response.status_code,
        }
    except requests.exceptions.ConnectionError:
        return {"status": "error", "message": f"Cannot connect to {conn.base_url}"}
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code
        if status == 401:
            msg = "Authentication failed. Check BW_USERNAME/BW_PASSWORD."
        elif status == 403:
            msg = "Access forbidden. Check user authorizations."
        else:
            msg = f"HTTP {status}: {e.response.reason}"
        return {"status": "error", "message": msg}
    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Connection timed out."}


@mcp.tool
def get_transformation_details(transformation_id: str) -> dict:
    """
    Get detailed metadata for a transformation including source, target,
    and rule information.

    Args:
        transformation_id: Technical ID of the transformation
            (e.g. "004BKVTJ2PRM1HA8G1Z7SB2E9VLVYK14").

    Returns:
        Dictionary with transformation details (source, target, rules, routines).
    """
    conn = BWConnection.from_env()
    path = f"/sap/bw/modeling/trfn/{transformation_id}"

    try:
        response = _bw_request(
            conn,
            path,
            accept="application/vnd.sap.bw.modeling.trfn-v1_0_0+xml",
        )
        # Return raw XML for now since transformation XML is complex
        root = ET.fromstring(response.text)

        details: dict = {
            "objectName": transformation_id,
            "objectType": "TRFN",
            "typeName": "Transformation",
            "rawXml": response.text,
        }

        # Try to extract basic info from the root element attributes
        for key, value in root.attrib.items():
            clean_key = key.split("}")[-1] if "}" in key else key
            details[clean_key] = value

        return details
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "object": transformation_id,
        }


# ---------------------------------------------------------------------------
# Query filter parsing helpers
# ---------------------------------------------------------------------------

# XML namespaces used specifically in the BW Query model response
QRY_NS = {
    "Qry": "http://www.sap.com/bw/Query.ecore",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "adtCore": "http://www.sap.com/adt/core",
}

_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

# Maps the query model's usageType to the BEx query designer filter terminology.
# - asFilter                -> "Fixed Filter" (hard restriction, not changeable at runtime)
# - asStartValue            -> "Default Value" (pre-filled default, user can override)
# - asStartValueAndFilter   -> characteristic is used as both a fixed filter and a default value
_FILTER_TYPE_LABELS = {
    "asFilter": "Fixed Filter",
    "asStartValue": "Default Value",
    "asStartValueAndFilter": "Fixed Filter + Default Value",
}


def _qry_localname(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _xsi_type(elem) -> str:
    """Return the local xsi:type of an element (without the Qry: prefix)."""
    t = elem.get(_XSI_TYPE, "")
    return t.split(":")[-1] if ":" in t else t


def _build_variable_map(root) -> dict:
    """Map variable component id (UID) -> {technicalName, description, ...}."""
    variables = {}
    for elem in root.iter():
        if _xsi_type(elem) == "Variable":
            uid = elem.get("id", "")
            desc = ""
            desc_elem = elem.find("Qry:description", QRY_NS)
            if desc_elem is not None:
                desc = desc_elem.get("value", "")
            variables[uid] = {
                "technicalName": elem.get("technicalName", ""),
                "description": desc,
                "infoObject": elem.get("infoObject", ""),
                "procType": (
                    elem.findtext("Qry:procType", default="", namespaces=QRY_NS) or ""
                ),
                "inputType": (
                    elem.findtext("Qry:inputType", default="", namespaces=QRY_NS) or ""
                ),
            }
    return variables


def _parse_range_boundary(elem, variables: dict) -> dict:
    """Parse a fromValue/toValue boundary of a SelectionRange.

    A boundary can be a literal value or a variable-driven value (with an
    optional shift/offset). Returns a dict describing the boundary.
    """
    if elem is None:
        return {}

    # Variable-driven boundary: has a <variable> child (UID) and type VariableXXX
    var_child = elem.find("Qry:variable", QRY_NS)
    value_child = elem.find("Qry:value", QRY_NS)
    type_child = elem.find("Qry:type", QRY_NS)

    if var_child is not None and var_child.text:
        uid = var_child.text.strip()
        var = variables.get(uid, {})
        return {
            "isVariable": True,
            "variableName": var.get("technicalName", "")
            or (value_child.text.strip() if value_child is not None and value_child.text else ""),
            "variableDescription": var.get("description", ""),
            "valueType": type_child.text.strip() if type_child is not None and type_child.text else "",
        }

    # Literal boundary: prefer internalValue attr, else <value> child text
    internal = elem.get("internalValue")
    literal = internal
    if not literal and value_child is not None and value_child.text:
        literal = value_child.text.strip()
    return {"isVariable": False, "value": literal or ""}


def _parse_range_token(token, variables: dict) -> dict:
    """Parse a SelectionRange token into a readable restriction dict."""
    from_b = _parse_range_boundary(token.find("Qry:fromValue", QRY_NS), variables)
    to_b = _parse_range_boundary(token.find("Qry:toValue", QRY_NS), variables)

    result = {
        "kind": "range",
        "operator": token.get("operator", ""),
        "exclude": token.get("exclude", "false") == "true",
        "selectionType": token.get("selectionType", ""),
        "fromValueDesc": token.get("fromValueDesc", ""),
        "toValueDesc": token.get("toValueDesc", ""),
        "from": from_b,
        "to": to_b,
    }

    # Capture shift/offset for variable-based date/period ranges when present
    if token.get("fromShift"):
        result["fromShift"] = token.get("fromShift")
    if token.get("toShift"):
        result["toShift"] = token.get("toShift")

    return result


def _parse_variable_token(token, variables: dict) -> dict:
    """Parse a SelectionVariable token, resolving the variable reference."""
    uid = token.get("variable", "")
    var = variables.get(uid, {})
    return {
        "kind": "variable",
        "operator": token.get("operator", ""),
        "exclude": token.get("exclude", "false") == "true",
        "selectionType": token.get("selectionType", ""),
        "variableName": var.get("technicalName", ""),
        "variableDescription": var.get("description", ""),
        "variableProcType": var.get("procType", ""),
    }


def _parse_selection_tokens(selection, variables: dict) -> list:
    """Extract restriction tokens (ranges/variables) directly under a selection."""
    restrictions = []
    for token in selection.findall("Qry:tokens", QRY_NS):
        ttype = _xsi_type(token)
        if ttype == "SelectionRange":
            restrictions.append(_parse_range_token(token, variables))
        elif ttype == "SelectionVariable":
            restrictions.append(_parse_variable_token(token, variables))
    return restrictions


def _parse_member_groups(member, variables: dict) -> list:
    """Parse the SelectionGroup restrictions inside a MemberSelection.

    A MemberSelection (used by restricted key figures and structure members)
    restricts one or more characteristics. Each <groups> element is a
    SelectionGroup for one characteristic (the key figure selector 1KYFNM
    identifies which base key figure the measure is built on), and its
    <tokens> children hold the actual value ranges / variables.
    """
    groups = []
    for group in member.findall("Qry:groups", QRY_NS):
        if _xsi_type(group) != "SelectionGroup":
            continue
        restrictions = _parse_selection_tokens(group, variables)
        if not restrictions:
            continue
        groups.append(
            {
                "infoObject": group.get("infoObject", ""),
                "description": group.get("description", ""),
                "restrictions": restrictions,
            }
        )
    return groups


def _member_description(member) -> str:
    """Return the description text of a MemberSelection, if present."""
    desc = member.find("Qry:description", QRY_NS)
    return desc.get("value", "") if desc is not None else ""


def _build_member_desc_map(root) -> dict:
    """Map member/measure element id -> description across the query document.

    Formula members reference their operands by internal id, so this lets us
    resolve a FormulaMemberOperand back to a readable measure name.
    """
    id_desc = {}
    for elem in root.iter():
        if _xsi_type(elem) not in ("MemberSelection", "MemberFormula", "RestrictedMeasure"):
            continue
        mid = elem.get("id", "")
        desc = elem.find("Qry:description", QRY_NS)
        if mid and desc is not None and desc.get("value"):
            id_desc[mid] = desc.get("value")
    return id_desc


def _member_base_key_figure(member) -> str:
    """Return the base key figure a MemberSelection restricts (via 1KYFNM)."""
    for group in member.findall("Qry:groups", QRY_NS):
        if group.get("infoObject") != "1KYFNM":
            continue
        token = group.find("Qry:tokens", QRY_NS)
        if token is None:
            continue
        from_elem = token.find("Qry:fromValue", QRY_NS)
        if from_elem is None:
            continue
        val_child = from_elem.find("Qry:value", QRY_NS)
        if val_child is not None and val_child.text:
            return val_child.text.strip()
    return ""


def _render_formula_token(token, id_desc: dict) -> str:
    """Recursively render a single formula token as a readable string.

    The formula is an expression tree: an operator token has operand tokens
    as its childToken children. Infix operators render as (a op b); prefix
    operators (functions like IF) render as CODE(arg, arg, ...).
    """
    ttype = _xsi_type(token)

    if ttype == "FormulaMemberOperand":
        mid = token.get("member", "")
        return f"'{id_desc.get(mid, mid)}'"
    if ttype == "FormulaConstant":
        return token.get("value", "")

    children = [c for c in list(token) if _qry_localname(c.tag) == "childToken"]
    rendered = [_render_formula_token(c, id_desc) for c in children]

    if ttype == "FormulaInfixOperator":
        op = token.get("code", "?")
        if len(rendered) == 2:
            return f"({rendered[0]} {op} {rendered[1]})"
        return f" {op} ".join(rendered)
    if ttype == "FormulaPrefixOperator":
        fn = token.get("code", "?")
        return f"{fn}(" + ", ".join(rendered) + ")"

    # Unknown token type: fall back to joining children
    return " ".join(rendered)


def _render_formula(formula_member, id_desc: dict) -> str:
    """Render a MemberFormula as a readable infix expression string.

    Member operands are resolved to their measure description; constants use
    their literal value; operators use their code (+, -, *, /, >, IF, ...).
    """
    definition = formula_member.find("Qry:formulaDefinition", QRY_NS)
    if definition is None:
        return ""
    root_token = definition.find("Qry:formulaToken", QRY_NS)
    if root_token is None:
        return ""
    expr = _render_formula_token(root_token, id_desc)
    # Strip a redundant outer paren pair for readability
    if expr.startswith("(") and expr.endswith(")"):
        expr = expr[1:-1]
    return expr


def _parse_query_details(xml_text: str) -> dict:
    """Parse key metadata from a Qry:queryResource response.

    The BW Query uses the Qry:queryResource schema rather than the Atom/
    properties format, so extract the useful attributes from the nested
    Query element and its entityProperties (authoring metadata).
    """
    root = ET.fromstring(xml_text)

    details: dict = {}

    # Locate the Query component element
    query_elem = None
    for elem in root.iter():
        if _xsi_type(elem) == "Query":
            query_elem = elem
            break

    if query_elem is None:
        return details

    # Description (child element with a "value" attribute)
    desc = query_elem.find("Qry:description", QRY_NS)
    if desc is not None and desc.get("value"):
        details["description"] = desc.get("value")

    # Selected attributes from the Query element
    attr_map = {
        "technicalName": "technicalName",
        "providerName": "infoProvider",
        "componentVersion": "componentVersion",
        "genUniID": "genUniID",
        "odataSupport": "odataSupport",
        "hanaView": "hanaView",
    }
    for src, dest in attr_map.items():
        if query_elem.get(src):
            details[dest] = query_elem.get(src)

    # Authoring metadata from entityProperties (adtCore namespace attributes)
    entity = query_elem.find("Qry:entityProperties", QRY_NS)
    if entity is not None:
        for src, dest in (
            ("responsible", "responsible"),
            ("createdBy", "createdBy"),
            ("createdAt", "createdAt"),
            ("changedBy", "changedBy"),
            ("changedAt", "changedAt"),
        ):
            val = entity.get(f"{{http://www.sap.com/adt/core}}{src}")
            if val:
                details[dest] = val
        status = entity.find("Qry:objectStatus", QRY_NS)
        if status is not None and status.text:
            details["objectStatus"] = status.text.strip()

    return details


# ---------------------------------------------------------------------------
# Query filter tool
# ---------------------------------------------------------------------------


@mcp.tool
def get_query_filters(query_name: str, object_version: str = "A") -> dict:
    """
    Read the filter restrictions defined on a BW Query.

    Complements get_query_structure: use get_query_structure for the query's
    characteristics, key figures, and measures; use this tool for the filter
    restrictions and restricted key figures. Together they give a full picture
    of how a query is built.

    Retrieves the query model from the BW Modeling API and extracts the global
    query filter: for each restricted characteristic it returns the fixed value
    ranges and/or the variable references that restrict it.

    Each filter is labelled with its BEx filter type (from usageType):
      - "Fixed Filter" (asFilter): a hard restriction the user cannot change
        at runtime (e.g. 0FISCVARNT = V5).
      - "Default Value" (asStartValue): a pre-filled default the user can
        override at runtime (e.g. 0CUST_GROUP = 05, 13, ...).
      - "Fixed Filter + Default Value" (asStartValueAndFilter): used as both.

    Args:
        query_name: Query technical name (e.g. "B080_V05_Q001").
        object_version: Object version, "A" (active, default) or "M" (modified).

    Returns:
        Dictionary with the query name, InfoProvider, and:
          - filters: the global query filter. Each entry has the InfoObject,
            usageType, filterType (BEx label), and its restrictions (fixed
            ranges with values, or resolved variables).
          - restrictedKeyFigures: reusable Restricted Key Figures (RKFs) with
            technicalName, description, and their per-characteristic
            restrictions.
          - structureMembers: local structure members (rows/columns) that
            carry selection restrictions, with description and restrictions.
            Pure formula members (no restriction) are omitted.
    """
    conn = BWConnection.from_env()
    version = (object_version or "A").upper()
    path = f"/sap/bw/modeling/query/{query_name}/{version}"

    try:
        response = _bw_request(
            conn,
            path,
            accept="application/vnd.sap.bw.modeling.query-v1_10_0+xml",
        )
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "query": query_name,
        }

    root = ET.fromstring(response.text)

    # Locate the Query component element
    query_elem = None
    for elem in root.iter():
        if _xsi_type(elem) == "Query":
            query_elem = elem
            break

    if query_elem is None:
        return {"error": "Query component not found in response", "query": query_name}

    variables = _build_variable_map(root)

    # Find the global <filter> child of the query
    filter_elem = None
    for child in list(query_elem):
        if _qry_localname(child.tag) == "filter":
            filter_elem = child
            break

    filters = []
    if filter_elem is not None:
        for selection in filter_elem.findall("Qry:selections", QRY_NS):
            if _xsi_type(selection) != "StandardFilterSelection":
                continue
            restrictions = _parse_selection_tokens(selection, variables)
            # Only report characteristics that actually carry a restriction
            if not restrictions:
                continue
            usage_type = selection.get("usageType", "")
            filters.append(
                {
                    "infoObject": selection.get("infoObject", ""),
                    "usageType": usage_type,
                    "filterType": _FILTER_TYPE_LABELS.get(usage_type, usage_type),
                    "restrictions": restrictions,
                }
            )

    # Restricted key figures: reusable RestrictedMeasure components, each
    # carrying a MemberSelection with the characteristic restrictions.
    restricted_key_figures = []
    for elem in root.iter():
        if _xsi_type(elem) != "RestrictedMeasure":
            continue
        member = elem.find("Qry:member", QRY_NS)
        groups = _parse_member_groups(member, variables) if member is not None else []
        restricted_key_figures.append(
            {
                "technicalName": elem.get("technicalName", ""),
                "description": _member_description(elem)
                or (
                    elem.find("Qry:description", QRY_NS).get("value", "")
                    if elem.find("Qry:description", QRY_NS) is not None
                    else ""
                ),
                "reusable": elem.get("reusable", "false") == "true",
                "restrictions": groups,
            }
        )

    # Structure members: local (non-reusable) selection members defined inside
    # a structure/CustomDimension (rows or columns). Only report members that
    # carry actual selection restrictions (skip pure formulas/empty members).
    # Members belonging to a RestrictedMeasure are excluded (reported above).
    parent_map = {c: p for p in root.iter() for c in p}
    structure_members = []
    for elem in root.iter():
        if _xsi_type(elem) != "MemberSelection":
            continue
        parent = parent_map.get(elem)
        if parent is not None and _xsi_type(parent) == "RestrictedMeasure":
            continue  # already covered as a restricted key figure
        groups = _parse_member_groups(elem, variables)
        if not groups:
            continue  # no real restriction (e.g. formula member)
        structure_members.append(
            {
                "description": _member_description(elem),
                "restrictions": groups,
            }
        )

    return {
        "query": query_elem.get("technicalName", query_name),
        "infoProvider": query_elem.get("providerName", ""),
        "objectVersion": version,
        "filterCount": len(filters),
        "filters": filters,
        "restrictedKeyFigureCount": len(restricted_key_figures),
        "restrictedKeyFigures": restricted_key_figures,
        "structureMemberCount": len(structure_members),
        "structureMembers": structure_members,
    }


# ---------------------------------------------------------------------------
# Query structure tool
# ---------------------------------------------------------------------------

# Maps a Dimension's axis (XML tag) to a readable axis name.
_AXIS_LABELS = {"rows": "row", "columns": "column", "free": "free"}


@mcp.tool
def get_query_structure(query_name: str, object_version: str = "A") -> dict:
    """
    List the characteristics and key figures used in a BW Query.

    Complements get_query_filters: use this tool for the query's characteristics,
    key figures, and measures; use get_query_filters for the filter restrictions
    and restricted key figures. Together they give a full picture of how a query
    is built.

    Retrieves the query model from the BW Modeling API and returns:
      - characteristics: every characteristic (InfoObject / navigation
        attribute) placed on the query, with its axis (row / column / free)
        and description.
      - keyFigures: the base key figures referenced by the query's measures
        (from the 1KYFNM key figure selector), with technical name and text.
      - measures: the displayed key figure structure members (columns), in
        display order. Each has a measureType:
          * "restricted" - a restricted measure; includes baseKeyFigure (the
            base key figure it restricts).
          * "calculated" - a calculated key figure; includes formula, a
            readable expression with operand measures resolved by name.

    Args:
        query_name: Query technical name (e.g. "B080_V05_Q001").
        object_version: Object version, "A" (active, default) or "M" (modified).

    Returns:
        Dictionary with the query name, InfoProvider, characteristics,
        key figures, and measures (restricted and calculated).
    """
    conn = BWConnection.from_env()
    version = (object_version or "A").upper()
    path = f"/sap/bw/modeling/query/{query_name}/{version}"

    try:
        response = _bw_request(
            conn,
            path,
            accept="application/vnd.sap.bw.modeling.query-v1_10_0+xml",
        )
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "query": query_name,
        }

    root = ET.fromstring(response.text)

    query_elem = None
    for elem in root.iter():
        if _xsi_type(elem) == "Query":
            query_elem = elem
            break

    if query_elem is None:
        return {"error": "Query component not found in response", "query": query_name}

    # Characteristics: Dimension elements placed on rows / columns / free axes.
    characteristics = []
    for child in list(query_elem):
        tag = _qry_localname(child.tag)
        if tag not in _AXIS_LABELS or _xsi_type(child) != "Dimension":
            continue
        iobj = child.get("infoObjectName", "")
        if not iobj:
            continue
        desc_elem = child.find("Qry:description", QRY_NS)
        characteristics.append(
            {
                "infoObject": iobj,
                "description": desc_elem.get("value", "") if desc_elem is not None else "",
                "axis": _AXIS_LABELS[tag],
            }
        )

    # Base key figures: referenced via SelectionRange tokens whose
    # selectionType is "keyFigure" (the 1KYFNM key figure selector).
    key_figures = {}
    for token in root.iter():
        if _xsi_type(token) != "SelectionRange":
            continue
        if token.get("selectionType") != "keyFigure":
            continue
        from_elem = token.find("Qry:fromValue", QRY_NS)
        if from_elem is None:
            continue
        val_child = from_elem.find("Qry:value", QRY_NS)
        kf = val_child.text.strip() if val_child is not None and val_child.text else ""
        if kf and kf not in key_figures:
            key_figures[kf] = token.get("fromValueDesc", "")

    key_figure_list = [
        {"keyFigure": kf, "description": desc} for kf, desc in key_figures.items()
    ]

    # Measures: the displayed key figure structure members. These live in the
    # CustomDimension (the key figure structure) and are of two kinds:
    #   - MemberSelection: a restricted measure (restricts a base key figure)
    #   - MemberFormula:   a calculated measure (arithmetic on other members)
    # Emit them in document (display) order, tagged by measureType.
    id_desc = _build_member_desc_map(root)
    measures = []
    for child in list(query_elem):
        if _xsi_type(child) != "CustomDimension":
            continue
        for member in list(child):
            mtype = _xsi_type(member)
            desc = _member_description(member)
            if not desc:
                continue
            if mtype == "MemberSelection":
                measures.append(
                    {
                        "description": desc,
                        "measureType": "restricted",
                        "baseKeyFigure": _member_base_key_figure(member),
                    }
                )
            elif mtype == "MemberFormula":
                measures.append(
                    {
                        "description": desc,
                        "measureType": "calculated",
                        "formula": _render_formula(member, id_desc),
                    }
                )

    return {
        "query": query_elem.get("technicalName", query_name),
        "infoProvider": query_elem.get("providerName", ""),
        "objectVersion": version,
        "characteristicCount": len(characteristics),
        "characteristics": characteristics,
        "keyFigureCount": len(key_figure_list),
        "keyFigures": key_figure_list,
        "measureCount": len(measures),
        "measures": measures,
    }


# ---------------------------------------------------------------------------
# Data flow / lineage tool
# ---------------------------------------------------------------------------


def _get_transformation_endpoints(transformation_id: str) -> dict:
    """Return the source and target of a transformation.

    Reads the TRFN model and returns {"source": {...}, "target": {...}} where
    each side has name, type (RSDS/ADSO/IOBJ/...), subType, and (for a
    DataSource source) the source system parsed from the name field.
    """
    conn = BWConnection.from_env()

    def _local(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    try:
        response = _bw_request(
            conn,
            f"/sap/bw/modeling/trfn/{transformation_id}",
            accept="application/vnd.sap.bw.modeling.trfn-v1_0_0+xml",
        )
    except requests.exceptions.HTTPError:
        return {}

    root = ET.fromstring(response.text)

    def _endpoint(elem):
        if elem is None:
            return {}
        attrs = {_local(k): v for k, v in elem.attrib.items()}
        # A DataSource (RSDS) name packs the DataSource + logical system,
        # e.g. "0COSTCENTER_ATTR              ECDCLNT200". Split on whitespace.
        raw_name = (attrs.get("name") or "").strip()
        ep = {
            "name": raw_name,
            "type": attrs.get("type", ""),
            "subType": attrs.get("subType", ""),
        }
        if attrs.get("type") == "RSDS":
            parts = raw_name.split()
            if len(parts) >= 2:
                ep["dataSource"] = parts[0]
                ep["sourceSystem"] = parts[-1]
            else:
                ep["dataSource"] = raw_name
        return ep

    source = next((c for c in list(root) if _local(c.tag) == "source"), None)
    target = next((c for c in list(root) if _local(c.tag) == "target"), None)
    return {"source": _endpoint(source), "target": _endpoint(target)}


def _get_dtp_endpoints(dtp_id: str) -> dict:
    """Return the source, target, and status of a Data Transfer Process.

    Reads the DTPA model and returns {"source": {...}, "target": {...},
    "objectStatus": ..., "contentState": ..., "version": ...}. Each endpoint
    has name and type; for a DataSource source (tlogo RSDS) the DataSource
    name and source system are parsed from the packed name field.
    """
    conn = BWConnection.from_env()

    def _local(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    try:
        response = _bw_request(
            conn,
            f"/sap/bw/modeling/dtpa/{dtp_id}",
            accept="application/vnd.sap.bw.modeling.dtpa-v1_0_0+xml",
        )
    except requests.exceptions.HTTPError:
        return {}

    root = ET.fromstring(response.text)

    def _endpoint(elem):
        if elem is None:
            return {}
        attrs = {_local(k): v for k, v in elem.attrib.items()}
        raw_name = (attrs.get("name") or "").strip()
        # A DTP source/target uses 'type' plus 'tlogo' (RSDS/ADSO/IOBJ/...).
        ep = {
            "name": raw_name,
            "type": attrs.get("tlogo", "") or attrs.get("type", ""),
        }
        if (attrs.get("tlogo") == "RSDS") or (attrs.get("type") == "DTASRC"):
            parts = raw_name.split()
            if len(parts) >= 2:
                ep["dataSource"] = parts[0]
                ep["sourceSystem"] = parts[-1]
            else:
                ep["dataSource"] = raw_name
        return ep

    source = next((c for c in root.iter() if _local(c.tag) == "source"), None)
    target = next((c for c in root.iter() if _local(c.tag) == "target"), None)

    result = {"source": _endpoint(source), "target": _endpoint(target)}

    # Status: objectStatus / contentState (activation) live in child elements,
    # and version ("active"/"revised") is an adtcore attribute in tlogoProperties.
    obj_status = next((c for c in root.iter() if _local(c.tag) == "objectStatus"), None)
    content_state = next((c for c in root.iter() if _local(c.tag) == "contentState"), None)
    if obj_status is not None and obj_status.text:
        result["objectStatus"] = obj_status.text.strip()
    if content_state is not None and content_state.text:
        result["contentState"] = content_state.text.strip()
    tlogo = next((c for c in root.iter() if _local(c.tag) == "tlogoProperties"), None)
    if tlogo is not None:
        ver = tlogo.get("{http://www.sap.com/adt/core}version") or tlogo.get("version")
        if ver:
            result["version"] = ver

    return result


@mcp.tool
def get_data_flow(object_name: str, object_type: str = "IOBJ") -> dict:
    """
    Show the data flow (lineage) around a BW object: what feeds INTO it and
    what it feeds OUT to, via transformations and their DataSources.

    Uses the BW Modeling where-used (xref) service to find related
    transformations, then reads each transformation's source and target to
    determine direction reliably (rather than parsing titles). For inbound
    transformations whose source is a DataSource, the DataSource name and
    source system are resolved.

    Args:
        object_name: Technical name of the object (e.g. "0COSTCENTER").
        object_type: Object type code (default "IOBJ"). Also e.g. ADSO, HCPR.

    Returns:
        Dictionary with:
          - inbound: transformations that load INTO the object (target = it),
            each with transformationId, source (dataSource/sourceSystem/type),
            and target subType (ATTR/TEXT/HIER for InfoObjects).
          - outbound: transformations that read the object OUT to another
            target (source = it), each with transformationId and target.
          - inboundDataTransferProcesses: DTPs whose TARGET is this object,
            each with dtp id, source, and status (objectStatus/contentState/
            version).
          - outboundDataTransferProcesses: DTPs whose SOURCE is this object,
            each with dtp id, target, and status.

    NOTE: process-chain usage of a DTP (which chains run it) is NOT available
    via the BW Modeling API; that relationship lives in the RSPCCHAIN table and
    requires table access (e.g. the ABAP ADT server), not this modeling API.
    """
    conn = BWConnection.from_env()
    NS = {
        "atom": "http://www.w3.org/2005/Atom",
        "bwModel": "http://www.sap.com/bw/modeling",
    }
    version = "A"

    try:
        response = _bw_request(
            conn,
            "/sap/bw/modeling/repo/is/xref",
            params={
                "objectType": object_type.upper(),
                "objectName": object_name,
                "objectVersion": version,
            },
            accept="application/xml",
        )
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "object": object_name,
        }

    root = ET.fromstring(response.text)

    transformation_ids = []
    dtp_names = []
    for entry in root.findall("atom:entry", NS):
        obj = entry.find("atom:content/bwModel:object", NS)
        if obj is None:
            continue
        otype = obj.get("objectType", "")
        oname = obj.get("objectName", "")
        if otype == "TRFN":
            transformation_ids.append(oname)
        elif otype == "DTPA":
            dtp_names.append(oname)

    target_name_upper = object_name.upper()

    inbound = []
    outbound = []
    for tid in transformation_ids:
        endpoints = _get_transformation_endpoints(tid)
        src = endpoints.get("source", {})
        tgt = endpoints.get("target", {})
        if tgt.get("name", "").upper() == target_name_upper:
            inbound.append(
                {
                    "transformationId": tid,
                    "source": src,
                    "targetSubType": tgt.get("subType", ""),
                }
            )
        elif src.get("name", "").upper().startswith(target_name_upper):
            outbound.append(
                {
                    "transformationId": tid,
                    "target": tgt,
                }
            )

    # Split DTPs by direction using each DTP's actual source/target, and
    # attach the DTP's status. A DTP is inbound when its target is this object.
    inbound_dtps = []
    outbound_dtps = []
    for dtp in dtp_names:
        endpoints = _get_dtp_endpoints(dtp)
        if not endpoints:
            continue
        src = endpoints.get("source", {})
        tgt = endpoints.get("target", {})
        status = {
            "objectStatus": endpoints.get("objectStatus", ""),
            "contentState": endpoints.get("contentState", ""),
            "version": endpoints.get("version", ""),
        }
        if tgt.get("name", "").upper() == target_name_upper:
            inbound_dtps.append({"dtp": dtp, "source": src, "status": status})
        elif src.get("name", "").upper().startswith(target_name_upper):
            outbound_dtps.append({"dtp": dtp, "target": tgt, "status": status})

    return {
        "object": object_name,
        "objectType": object_type.upper(),
        "inboundCount": len(inbound),
        "inbound": inbound,
        "outboundCount": len(outbound),
        "outbound": outbound,
        "inboundDataTransferProcessCount": len(inbound_dtps),
        "inboundDataTransferProcesses": inbound_dtps,
        "outboundDataTransferProcessCount": len(outbound_dtps),
        "outboundDataTransferProcesses": outbound_dtps,
    }


# ---------------------------------------------------------------------------
# InfoArea tree tool
# ---------------------------------------------------------------------------


_INFOPROVIDER_STRUCTURE_ROOT = "/sap/bw/modeling/repo/infoproviderstructure/area"

# Namespaces for the InfoProvider structure atom feeds.
_STRUCT_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "bwModel": "http://www.sap.com/bw/modeling",
}
_CHILDREN_REL = "http://www.sap.com/bw/modeling/relations:children"


def _children_href(entry) -> str:
    """Return the 'children' link href of a structure entry, if present."""
    link = entry.find(f"atom:link[@rel='{_CHILDREN_REL}']", _STRUCT_NS)
    return link.get("href", "") if link is not None else ""


def _fetch_structure_feed(conn, href: str):
    """Fetch and parse an InfoProvider-structure atom feed at the given href."""
    response = _bw_request(conn, href, accept="application/xml")
    return ET.fromstring(response.text)


def _fetch_infoarea_node(conn, area: str):
    """Fetch the contents of one InfoArea node. Returns (subAreas, objects).

    subAreas: list of {name, description} child InfoAreas to recurse into.
    objects:  list of {name, type, typeName, description} objects assigned to
              this area. Objects are grouped under semanticalFolder nodes (by
              kind, e.g. "HCPR", "Characteristic"); this follows each folder's
              children link to collect them.
    """
    sub_areas = []
    objects = []

    root = _fetch_structure_feed(conn, f"{_INFOPROVIDER_STRUCTURE_ROOT}/{area.lower()}")

    for entry in root.findall("atom:entry", _STRUCT_NS):
        obj = entry.find("atom:content/bwModel:object", _STRUCT_NS)
        if obj is None:
            continue
        otype = obj.get("objectType", "")
        oname = obj.get("objectName", "")
        title = entry.findtext("atom:title", namespaces=_STRUCT_NS) or ""

        if otype == "AREA":
            sub_areas.append({"name": oname, "description": title})
        elif otype == "semanticalFolder":
            # Objects hang off the folder's children feed; fetch and collect.
            href = _children_href(entry)
            if not href:
                continue
            try:
                folder_root = _fetch_structure_feed(conn, href)
            except requests.exceptions.HTTPError:
                continue
            for fentry in folder_root.findall("atom:entry", _STRUCT_NS):
                fobj = fentry.find("atom:content/bwModel:object", _STRUCT_NS)
                if fobj is None:
                    continue
                ftype = fobj.get("objectType", "")
                if ftype in ("AREA", "semanticalFolder"):
                    continue
                objects.append(
                    {
                        "name": fobj.get("objectName", ""),
                        "type": ftype,
                        "typeName": OBJECT_TYPES.get(ftype, ftype),
                        "description": fentry.findtext(
                            "atom:title", namespaces=_STRUCT_NS
                        )
                        or "",
                    }
                )
        else:
            objects.append(
                {
                    "name": oname,
                    "type": otype,
                    "typeName": OBJECT_TYPES.get(otype, otype),
                    "description": title,
                }
            )

    return sub_areas, objects


@mcp.tool
def get_infoarea_tree(info_area: str, recursive: bool = True, max_depth: int = 10) -> dict:
    """
    List the objects contained in an InfoArea using the InfoArea node tree.

    This walks the BW InfoProvider structure (the InfoArea hierarchy as shown in
    the modeling tools), which is the reliable way to see what belongs to an
    InfoArea. InfoAreas nest: e.g. B080 ("Sales and Distribution") contains
    sub-areas like B080_V, B080_D, B080_CM, whose leaf objects are the actual
    ADSOs, CompositeProviders, and so on.

    NOTE: prefer this over get_infoarea_contents for "what is under InfoArea X" -
    get_infoarea_contents uses a generic search whose InfoArea filter is
    unreliable and returns only a flat, unfiltered list.

    Args:
        info_area: InfoArea technical name (e.g. "B080").
        recursive: If true (default), descend into sub-areas. If false, only the
            direct children of the given InfoArea are returned.
        max_depth: Safety limit on recursion depth (default 10).

    Returns:
        Dictionary with the InfoArea, a flat list of all objects found (with
        the sub-area each was found in), a per-type count summary, and the list
        of sub-areas encountered.
    """
    conn = BWConnection.from_env()

    all_objects = []
    all_sub_areas = []
    visited = set()

    def _walk(area: str, depth: int):
        if area in visited or depth > max_depth:
            return
        visited.add(area)
        try:
            sub_areas, objects = _fetch_infoarea_node(conn, area)
        except requests.exceptions.HTTPError:
            return
        for obj in objects:
            obj_with_area = dict(obj)
            obj_with_area["infoArea"] = area
            all_objects.append(obj_with_area)
        for sub in sub_areas:
            all_sub_areas.append({"name": sub["name"], "description": sub["description"], "parent": area})
            if recursive:
                _walk(sub["name"], depth + 1)

    _walk(info_area, 0)

    type_counts = {}
    for obj in all_objects:
        type_counts[obj["type"]] = type_counts.get(obj["type"], 0) + 1

    return {
        "infoArea": info_area,
        "recursive": recursive,
        "objectCount": len(all_objects),
        "typeCounts": type_counts,
        "subAreas": all_sub_areas,
        "objects": all_objects,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Console-script entry point: start the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
