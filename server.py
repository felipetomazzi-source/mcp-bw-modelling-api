"""
MCP Server for SAP BW Modeling API

Exposes SAP BW4/HANA modeling resources (InfoAreas, InfoObjects, ADSOs,
CompositeProviders, Queries, etc.) as MCP tools via FastMCP.
"""

import copy
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

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
        "                       display, compounding, data type). For the\n"
        "                       actual master-data VALUES of a characteristic\n"
        "                       (e.g. list 0CUSTOMER/0MATERIAL values, optionally\n"
        "                       restricted to an InfoProvider) use\n"
        "                       get_characteristic_values.\n"
        "     - ADSO         -> get_adso_fields (field/InfoObject list)\n"
        "     - HCPR         -> get_composite_provider_parts (source providers,\n"
        "                       joins; include_nodes / include_mappings /\n"
        "                       include_lineage for node graph, field\n"
        "                       assignments and field lineage)\n"
        "     - ABAP code    -> search_abap_objects / get_abap_source\n"
        "                       (programs, classes + methods, FMs, CDS;\n"
        "                       e.g. a HANA transformation's AMDP class\n"
        "                       classNameA, method GLOBAL_END)\n"
        "     - Query        -> get_query_structure (characteristics, key\n"
        "                       figures, measures) AND get_query_filters\n"
        "                       (fixed/default filters, restricted key figures).\n"
        "                       For the query's actual RESULT DATA (executed\n"
        "                       figures, not the definition) use read_query_data.\n"
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
    except requests.exceptions.HTTPError as e:
        return [{"error": f"HTTP {e.response.status_code}: {e.response.reason}", "object": object_name}]

    root = ET.fromstring(response.text)

    def _local(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    def _xsi(elem) -> str:
        v = elem.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
        return v.split(":")[-1] if ":" in v else v

    # The ADSO model (adso:dataStore) lists fields as <element> children with
    # xsi:type "adso:AdsoElement". <keyElement> children mark the key fields;
    # their key InfoObject reference is in a keyInfoObjectName / text form.
    # The element's dimension attribute encodes its kind: CHA=characteristic,
    # TIM=time, UINI/UNI=unit/currency, KYF=key figure.
    dim_kind = {
        "CHA": "characteristic",
        "TIM": "time",
        "UINI": "unit",
        "UNI": "unit",
        "KYF": "keyfigure",
    }

    # Collect the set of key field names from <keyElement> entries. The key is
    # given as a reference path in the element text, e.g. "#///0BILL_NUM"; the
    # field name is the last path segment.
    key_names = set()
    for elem in root.iter():
        if _local(elem.tag) != "keyElement":
            continue
        ref = (
            elem.get("keyInfoObjectName")
            or elem.get("infoObjectName")
            or elem.get("name")
            or (elem.text.strip() if elem.text and elem.text.strip() else "")
        )
        if ref:
            # Take the last segment of a "#///NAME" style reference.
            key_names.add(ref.split("/")[-1])

    fields = []
    for elem in root.iter():
        if _xsi(elem) != "AdsoElement":
            continue
        iobj = elem.get("infoObjectName", "") or elem.get("name", "")
        # dimension attr looks like "#///CHA" (may have a trailing marker char).
        raw_dim = elem.get("dimension", "")
        dim_code = ""
        if raw_dim:
            tail = raw_dim.rstrip().split("/")[-1]
            dim_code = "".join(ch for ch in tail if ch.isalpha())
        fields.append(
            {
                "name": elem.get("name", ""),
                "infoObject": iobj,
                "baseInfoObject": elem.get("baseInfoObjectName", ""),
                "kind": dim_kind.get(dim_code, dim_code or ""),
                "isKey": (elem.get("name", "") in key_names) or (iobj in key_names),
                "aggregation": elem.get("aggregationBehavior", ""),
                "conversionRoutine": elem.get("conversionRoutine", ""),
                "outputLength": elem.get("outputLength", ""),
            }
        )

    return fields


def _hcpr_local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _hcpr_xsi(elem) -> str:
    v = elem.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
    return v.split(":")[-1] if ":" in v else v


def _hcpr_ref_target(ref: str) -> str:
    """Return the last path segment of a join input reference.

    Join input refs look like ``#///J1/J1.ADSO.1`` (a source alias) or
    ``#///J2/J1`` (another join node's name). The meaningful token is the last
    path segment.
    """
    return ref.rsplit("/", 1)[-1] if ref else ""


def _parse_hcpr_join_conditions(root, alias_to_name: dict) -> list[dict]:
    """Parse the Join node conditions from a CompositeProvider model.

    Each join <viewNode> (xsi:type View:JoinNode) contains <join> elements with
    joinType / cardinality / leftInput / rightInput attributes and paired
    <leftElementName> / <rightElementName> children (the ON-condition column
    pairs). Returns one entry per join with its node, sides (resolved to the
    underlying provider name where the input is a source alias), and the list
    of field pairs.
    """
    parent = {c: p for p in root.iter() for c in p}
    conditions = []

    def _resolve_side(ref: str) -> dict:
        token = _hcpr_ref_target(ref)
        side = {"alias": token}
        name = alias_to_name.get(token, "")
        # Attach provider only for a real underlying source (where the mapped
        # name differs from the alias). A nested join node maps to itself
        # (alias == name, e.g. "J1"), so leave provider out and flag it.
        if name and name != token:
            side["provider"] = name
        else:
            side["isNestedNode"] = True
        return side

    for node in root.iter():
        if _hcpr_local(node.tag) != "viewNode" or _hcpr_xsi(node) != "JoinNode":
            continue
        node_name = node.get("name", "")
        for j in node:
            if _hcpr_local(j.tag) != "join":
                continue
            lefts = [
                c.text
                for c in j
                if _hcpr_local(c.tag) == "leftElementName" and c.text
            ]
            rights = [
                c.text
                for c in j
                if _hcpr_local(c.tag) == "rightElementName" and c.text
            ]
            field_pairs = [
                {"left": l, "right": r} for l, r in zip(lefts, rights)
            ]
            conditions.append(
                {
                    "node": node_name,
                    "joinType": j.get("joinType", ""),
                    "cardinality": j.get("cardinality", ""),
                    "leftSide": _resolve_side(j.get("leftInput", "")),
                    "rightSide": _resolve_side(j.get("rightInput", "")),
                    "on": field_pairs,
                }
            )
    return conditions


# ---------------------------------------------------------------------------
# CompositeProvider model analysis (node graph, field mappings, lineage)
# ---------------------------------------------------------------------------


def _hcpr_collapse(text: str) -> str:
    """Collapse whitespace/newlines of a SQL formula into a single line."""
    return " ".join((text or "").split())


def _hcpr_elem_label(elem) -> str:
    """Return the best available description of a view-node <element>."""
    for c in elem:
        if _hcpr_local(c.tag) == "endUserTexts" and c.get("label"):
            return c.get("label")
    for c in elem:
        if _hcpr_local(c.tag) == "localProperties":
            for d in c:
                if _hcpr_local(d.tag) == "descriptions" and d.get("label"):
                    return d.get("label")
    return ""


def _hcpr_elem_formula(elem) -> str:
    for c in elem:
        if _hcpr_local(c.tag) == "formulaExpression":
            for f in c:
                if _hcpr_local(f.tag) == "formula":
                    return _hcpr_collapse(f.text)
    return ""


def _hcpr_clean_source(source: str, provider: str) -> str:
    """Strip the generated prefix from a part-provider field name.

    Open ODS View fields appear as ``2F<provider>-<FIELD>`` (and the view's key
    characteristic as bare ``2F<provider>``); field-based ADSO fields as
    ``4<provider>-<FIELD>``. InfoObject-based fields have no prefix.
    """
    if not source or not provider:
        return source
    m = re.match(rf"^\d[A-Z]?{re.escape(provider)}(-(.*))?$", source)
    if not m:
        return source
    return m.group(2) if m.group(2) else f"{provider} (key)"


def _hcpr_provider_type(alias: str) -> str:
    """Derive the part-provider type from its alias (e.g. U1.ADSO.5 -> ADSO)."""
    parts = (alias or "").split(".")
    return parts[1] if len(parts) >= 3 else ""


class _HcprModel:
    """Parsed view-node graph of a CompositeProvider model."""

    def __init__(self, root):
        self.root = root
        self.nodes: dict = {}
        for vn in root:
            if _hcpr_local(vn.tag) == "viewNode":
                self.nodes[vn.get("name", "")] = vn
        default_ref = root.get("defaultNode", "")
        self.output_node = _hcpr_ref_target(default_ref) if default_ref else ""
        if not self.output_node and self.nodes:
            self.output_node = next(iter(self.nodes))

        # Per node: parsed inputs, elements, filter, joins.
        self.inputs: dict = {}
        self.elements: dict = {}
        self.filters: dict = {}
        self.joins: dict = {}
        # consumers[child_node] -> list of (parent_node, {source: [targets]})
        self.consumers: dict = {}
        for name, vn in self.nodes.items():
            self.inputs[name] = [self._parse_input(i) for i in vn if _hcpr_local(i.tag) == "input"]
            self.elements[name] = [self._parse_element(e) for e in vn if _hcpr_local(e.tag) == "element"]
            for ch in vn:
                t = _hcpr_local(ch.tag)
                if t == "filterExpression":
                    self.filters[name] = _hcpr_collapse(
                        next((f.text for f in ch if _hcpr_local(f.tag) == "formula"), "")
                    )
                elif t == "join":
                    lefts = [c.text for c in ch if _hcpr_local(c.tag) == "leftElementName" and c.text]
                    rights = [c.text for c in ch if _hcpr_local(c.tag) == "rightElementName" and c.text]
                    self.joins.setdefault(name, []).append(
                        {
                            "joinType": ch.get("joinType", ""),
                            "cardinality": ch.get("cardinality", ""),
                            "left": _hcpr_ref_target(ch.get("leftInput", "")),
                            "right": _hcpr_ref_target(ch.get("rightInput", "")),
                            "on": [{"left": l, "right": r} for l, r in zip(lefts, rights)],
                        }
                    )
            for inp in self.inputs[name]:
                if inp["kind"] == "node":
                    fwd: dict = {}
                    for m in inp["mappings"]:
                        if m["source"]:
                            fwd.setdefault(m["source"], []).append(m["target"])
                    self.consumers.setdefault(inp["name"], []).append((name, fwd))

    @staticmethod
    def _parse_input(inp) -> dict:
        nested = next((c.text for c in inp if _hcpr_local(c.tag) == "viewNode"), "")
        entity = next((c.text for c in inp if _hcpr_local(c.tag) == "entity"), "")
        name = inp.get("name", "")
        alias = inp.get("alias", "")
        is_node = bool(nested) and not entity
        mappings = []
        for m in inp:
            if _hcpr_local(m.tag) != "mapping":
                continue
            mtype = _hcpr_xsi(m)
            entry = {
                "source": m.get("sourceName", ""),
                "target": m.get("targetName", ""),
            }
            if mtype and mtype != "ElementMapping":
                entry["mappingType"] = mtype
            const = m.get("value") or m.get("constant") or m.get("constantValue")
            if const is not None:
                entry["constant"] = const
            mappings.append(entry)
        return {
            "name": _hcpr_ref_target(nested) if is_node else name,
            "alias": alias,
            "kind": "node" if is_node else "provider",
            "providerType": "" if is_node else _hcpr_provider_type(alias),
            "mappings": mappings,
        }

    @staticmethod
    def _parse_element(e) -> dict:
        inline = next((c for c in e if _hcpr_local(c.tag) == "inlineType"), None)
        dtype = ""
        if inline is not None:
            dtype = inline.get("name", "")
            if inline.get("length"):
                dtype += f"({inline.get('length')})"
            elif inline.get("precision"):
                dtype += f"({inline.get('precision')},{inline.get('scale', '0')})"
        calculated = _hcpr_xsi(e) == "CalculatedBwElement"
        el = {
            "name": e.get("name", ""),
            "infoObject": e.get("infoObjectName", ""),
            "label": _hcpr_elem_label(e),
            "dataType": dtype,
        }
        if calculated:
            formula = _hcpr_elem_formula(e)
            el["calculated"] = True
            el["formula"] = formula
            el["formulaFields"] = sorted(set(re.findall(r'"([^"]+)"', formula)))
        return el

    # -- lineage ----------------------------------------------------------

    def trace_back(self, node: str, field: str, _seen=None) -> list:
        """Return the origins of ``field`` as exposed by ``node``."""
        _seen = _seen or set()
        key = (node, field)
        if key in _seen:
            return []
        _seen = _seen | {key}
        origins = []
        calc = next(
            (e for e in self.elements.get(node, []) if e["name"] == field and e.get("calculated")),
            None,
        )
        if calc:
            origins.append(
                {
                    "calculatedIn": node,
                    "formula": calc["formula"],
                    "formulaFields": calc["formulaFields"],
                    # Where each field read by the formula comes from.
                    "formulaFieldOrigins": {
                        f: self.compact_origins(node, f)
                        for f in calc["formulaFields"]
                        if f != field
                    },
                }
            )
        for inp in self.inputs.get(node, []):
            for m in inp["mappings"]:
                if m["target"] != field:
                    continue
                if "constant" in m and not m["source"]:
                    origins.append({"node": node, "constant": m["constant"]})
                    continue
                if inp["kind"] == "provider":
                    o = {
                        "provider": inp["name"],
                        "providerType": inp["providerType"],
                        "alias": inp["alias"],
                        "sourceField": m["source"],
                        "enteredAt": node,
                        "path": [f"{node}.{field}"],
                    }
                    clean = _hcpr_clean_source(m["source"], inp["name"])
                    if clean != m["source"]:
                        o["sourceFieldName"] = clean
                    origins.append(o)
                else:
                    for sub in self.trace_back(inp["name"], m["source"], _seen):
                        sub = dict(sub)
                        if "path" in sub:
                            sub["path"] = [f"{node}.{field}"] + sub["path"]
                        origins.append(sub)
        return origins

    def trace_forward(self, node: str, field: str, _seen=None) -> list:
        """Return the output-node field names that ``node.field`` reaches."""
        if node == self.output_node:
            return [field]
        _seen = _seen or set()
        if (node, field) in _seen:
            return []
        _seen = _seen | {(node, field)}
        out = []
        for parent, fwd in self.consumers.get(node, []):
            for tgt in fwd.get(field, []):
                out.extend(self.trace_forward(parent, tgt, _seen))
        return sorted(set(out))

    def formula_uses(self, node: str, field: str) -> list:
        """Calculated fields (at ``node`` or any node above) whose formula
        reads ``node.field`` as it propagates upward."""
        uses = []
        seen = set()
        stack = [(node, field)]
        while stack:
            n, f = stack.pop()
            if (n, f) in seen:
                continue
            seen.add((n, f))
            for e in self.elements.get(n, []):
                if e.get("calculated") and f in e.get("formulaFields", []):
                    uses.append(
                        {
                            "node": n,
                            "calculatedField": e["name"],
                            "outputFields": self.trace_forward(n, e["name"]),
                        }
                    )
            for parent, fwd in self.consumers.get(n, []):
                for tgt in fwd.get(f, []):
                    stack.append((parent, tgt))
        return uses

    def compact_origins(self, node: str, field: str) -> list:
        """Origins of node.field as short strings, e.g. 'B020_O28.TEL_NUMBER'."""
        out = []
        for o in self.trace_back(node, field):
            if "provider" in o:
                out.append(f"{o['provider']}.{o.get('sourceFieldName', o['sourceField'])}")
            elif "calculatedIn" in o:
                out.append(f"calculated@{o['calculatedIn']}")
            elif "constant" in o:
                out.append(f"constant '{o['constant']}'")
        return sorted(set(out))

    # -- views ------------------------------------------------------------

    def node_summaries(self) -> list:
        result = []
        for name in self.nodes:
            elems = self.elements[name]
            entry = {
                "name": name,
                "type": _hcpr_xsi(self.nodes[name]),
                "isOutputNode": name == self.output_node,
                "fieldCount": len(elems),
                "inputs": [
                    {
                        k: v
                        for k, v in {
                            "name": i["name"],
                            "kind": i["kind"],
                            "providerType": i["providerType"],
                            "alias": i["alias"],
                            "mappedFieldCount": len(i["mappings"]),
                        }.items()
                        if v != ""
                    }
                    for i in self.inputs[name]
                ],
            }
            if name in self.filters:
                entry["filter"] = self.filters[name]
            if name in self.joins:
                entry["joins"] = self.joins[name]
            calcs = [
                {k: e[k] for k in ("name", "infoObject", "label", "formula", "formulaFields") if e.get(k)}
                for e in elems
                if e.get("calculated")
            ]
            if calcs:
                entry["calculatedFields"] = calcs
            iobjs = [e["name"] for e in elems if e["infoObject"]]
            entry["infoObjectFieldCount"] = len(iobjs)
            result.append(entry)
        return result

    def node_tree(self) -> str:
        """Render the node graph (from the output node down) as indented text."""
        lines: list = []

        def render(name: str, prefix: str, connector: str, side: str, seen: set):
            vn_type = _hcpr_xsi(self.nodes.get(name)) if name in self.nodes else ""
            desc = f"{name} [{vn_type}]"
            for j in self.joins.get(name, []):
                on = ", ".join(
                    p["left"] if p["left"] == p["right"] else f"{p['left']}={p['right']}"
                    for p in j["on"]
                )
                desc += f" {j['joinType']} {j['cardinality']} ON {on}"
            if name in self.filters:
                desc += f"  FILTER {self.filters[name]}"
            calcs = [e["name"] for e in self.elements.get(name, []) if e.get("calculated")]
            if calcs:
                desc += f"  CALC {', '.join(calcs)}"
            lines.append(f"{prefix}{connector}{side}{desc}")
            if name in seen:
                return
            seen = seen | {name}
            if connector == "":
                child_prefix = prefix
            else:
                child_prefix = prefix + ("   " if connector == "└─ " else "│  ")
            join = (self.joins.get(name) or [{}])[0]
            # Show the join's left input first, then the right one.
            inputs = sorted(
                self.inputs.get(name, []),
                key=lambda i: 1 if join and (i["alias"] or i["name"]) == join.get("right") else 0,
            )
            for idx, inp in enumerate(inputs):
                conn_ = "└─ " if idx == len(inputs) - 1 else "├─ "
                s = ""
                token = inp["alias"] or inp["name"]
                if join:
                    if token == join.get("left"):
                        s = "(left) "
                    elif token == join.get("right"):
                        s = "(right) "
                if inp["kind"] == "node":
                    render(inp["name"], child_prefix, conn_, s, seen)
                else:
                    lines.append(
                        f"{child_prefix}{conn_}{s}{inp['name']} "
                        f"({inp['providerType'] or 'provider'}, alias {inp['alias']}, "
                        f"{len(inp['mappings'])} fields)"
                    )

        if self.output_node:
            render(self.output_node, "", "", "", set())
        return "\n".join(lines)


@mcp.tool
def get_composite_provider_parts(
    object_name: str,
    include_join_conditions: bool = True,
    include_nodes: bool = False,
    include_mappings: bool = False,
    include_lineage: bool = False,
    field_filter: str = "",
    only_renamed: bool = False,
) -> dict:
    """
    Get the part providers (data sources) of a CompositeProvider, the join
    conditions between them, and - on request - the full node graph, the
    field mappings from each part provider, and field-level lineage.

    A CompositeProvider combines several source InfoProviders (ADSOs,
    InfoObjects, Open ODS Views) via Union, Join and Projection nodes, which
    can be nested (e.g. J6 <- J5 <- J3 <- ... <- P1 <- Open ODS View). Each
    node input maps its source fields to the node's fields, and projections
    can carry SQL filters and calculated fields.

    WHAT TO REQUEST:
      - "Which providers / joins?"            -> defaults.
      - "How are the nodes built / filtered?"  -> include_nodes=True
        (adds nodeTree text + per-node filters, joins, calculated fields).
      - "Which source field goes to which CompositeProvider field?"
                                               -> include_mappings=True
        (per part provider: source field -> field at entry node -> output
        field(s); fields not reaching the output are flagged).
      - "Where does output field X come from?" -> include_lineage=True
        (per output field: every origin provider field, calculated-field
        formula or constant, with the node path; plus unassignedFields).
      Narrow big models with field_filter (e.g. "CUSTOMER") and/or
      only_renamed=True (mappings where the field name changes).

    Args:
        object_name: Technical name of the CompositeProvider (e.g. "B080_V05").
        include_join_conditions: If True (default), also return the join
            conditions under ``joinConditions``. Set to False to get only the
            part-provider list (e.g. for a pure Union provider with no joins).
        include_nodes: If True, return ``nodeTree`` (an indented text rendering
            of the node graph from the output node down, with join types, ON
            fields, filters and calculated fields) and ``nodes`` (structured
            per-node detail: type, inputs, filter, joins, calculated fields with
            formulas, field counts).
        include_mappings: If True, return ``mappings``: one entry per part
            provider input with its field assignments. Each assignment has
            sourceField (raw name in the model), sourceFieldName (cleaned name,
            when the raw name has a generated prefix such as "2FB020_O14-"),
            targetField (field at the node where the provider enters) and
            outputFields (the CompositeProvider output field(s) it ends up in),
            feedsCalculatedFields (calculated fields whose formula reads it,
            e.g. ADR2 TEL_NUMBER -> MOBILEPH), and notInOutput (true when the
            field reaches neither an output field nor a formula - typically a
            join key or projection-filter-only field).
        include_lineage: If True, return ``lineage``: for each field of the
            output node, its InfoObject, label, data type, and ``origins`` -
            each a provider field (provider, sourceField, enteredAt node, path),
            a calculated field (calculatedIn, formula, formulaFields, and
            formulaFieldOrigins mapping each formula field to its provider
            fields) or a constant. Join-key fields list one origin per joined
            side. The full lineage of a big join model can be ~50 KB - use
            field_filter when you only need some fields. For a Union output
            node each field also gets ``notFedBy`` (part providers that deliver
            it empty). Also returns ``unassignedFields`` (output fields with no
            origin at all - always empty in queries).
        field_filter: Case-insensitive substring. Limits mappings to
            assignments whose source/target/output field matches, and lineage to
            output fields whose name, InfoObject or label (or any origin source
            field) matches.
        only_renamed: If True, mappings keep only assignments where the field
            name changes on the way (cleaned source != target, or the output
            field differs from the target) - the "interesting" assignments.

    Returns:
        Dictionary with:
          - object: the CompositeProvider name
          - outputNode: the node exposed for reporting (e.g. "U1", "J6")
          - providers: the distinct leaf part providers, each with name,
            providerType (ADSO / FBPA (Open ODS View) / IOBJ ...), and the
            aliases/nodes where it is used (one provider can be used several
            times, e.g. in different projections).
          - parts: list of all node inputs, each with name, alias, kind
            ("provider" = a real part provider, "node" = a nested node feeding
            this one) and combination (the type of the node it feeds:
            "Union" / "JoinNode" / "Projection" / "Aggregation").
          - joinConditions (when include_join_conditions is on): one entry per
            join, each with:
              * node: the join node name (e.g. "J1")
              * joinType: "inner" / "leftOuter" / ...
              * cardinality: e.g. "C1_N", "CN_1", "CN_N"
              * leftSide / rightSide: the joined inputs. A source input has
                {alias, provider} (provider = underlying source name, e.g.
                B700_D01). A nested join input has {alias, isNestedNode: true}
                where alias is that join node's name (e.g. "J1"), i.e. the
                result of a deeper join feeds into this one.
              * on: list of {left, right} field pairs (the ON condition)
          - joinConditionCount: number of join conditions (when included)
          - nodeTree / nodes (include_nodes), mappings (include_mappings),
            lineage / unassignedFields (include_lineage) - see Args.
    """
    conn = BWConnection.from_env()

    path = f"/sap/bw/modeling/hcpr/{object_name}"

    try:
        response = _bw_request(
            conn, path, accept="application/vnd.sap.bw.modeling.hcpr-v1_15_0+xml"
        )
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "object": object_name,
        }

    root = ET.fromstring(response.text)

    # The CompositeProvider model (Composite:compositeView) nests source
    # providers as <input> elements (xsi:type Composite:CompositeInput) inside
    # viewNode elements whose xsi:type is View:Union / View:JoinNode / etc.
    parts = []
    alias_to_name: dict = {}
    for node in root.iter():
        if _hcpr_local(node.tag) != "viewNode":
            continue
        combination = _hcpr_xsi(node)  # e.g. "Union" -> from "View:Union"
        for child in list(node):
            if _hcpr_local(child.tag) != "input":
                continue
            name = child.get("name", "")
            alias = child.get("alias", "")
            if alias:
                alias_to_name[alias] = name
            is_node = any(
                _hcpr_local(c.tag) == "viewNode" for c in child
            ) and not any(_hcpr_local(c.tag) == "entity" and c.text for c in child)
            parts.append(
                {
                    "name": name,
                    "alias": alias,
                    "kind": "node" if is_node else "provider",
                    "combination": combination,
                }
            )

    model = _HcprModel(root)

    providers: dict = {}
    for node_name, inputs in model.inputs.items():
        for inp in inputs:
            if inp["kind"] != "provider":
                continue
            p = providers.setdefault(
                inp["name"],
                {"name": inp["name"], "providerType": inp["providerType"], "usedIn": []},
            )
            p["usedIn"].append({"node": node_name, "alias": inp["alias"]})

    result: dict = {
        "object": object_name,
        "outputNode": model.output_node,
        "providers": list(providers.values()),
        "parts": parts,
    }

    if include_join_conditions:
        join_conditions = _parse_hcpr_join_conditions(root, alias_to_name)
        result["joinConditionCount"] = len(join_conditions)
        result["joinConditions"] = join_conditions

    flt = field_filter.strip().upper()

    if include_nodes:
        result["nodeTree"] = model.node_tree()
        result["nodes"] = model.node_summaries()

    if include_mappings:
        mappings = []
        total = 0
        for node_name, inputs in model.inputs.items():
            for inp in inputs:
                if inp["kind"] != "provider":
                    continue
                rows = []
                for m in inp["mappings"]:
                    total += 1
                    clean = _hcpr_clean_source(m["source"], inp["name"])
                    outputs = model.trace_forward(node_name, m["target"])
                    row = {"sourceField": m["source"]}
                    if clean != m["source"]:
                        row["sourceFieldName"] = clean
                    row["targetField"] = m["target"]
                    row["outputFields"] = outputs
                    uses = model.formula_uses(node_name, m["target"])
                    if uses:
                        row["feedsCalculatedFields"] = uses
                    if not outputs and not uses:
                        row["notInOutput"] = True
                    for k in ("mappingType", "constant"):
                        if k in m:
                            row[k] = m[k]
                    if only_renamed and clean == m["target"] and outputs == [m["target"]]:
                        continue
                    if flt and not any(
                        flt in (v or "").upper()
                        for v in [m["source"], clean, m["target"], *outputs]
                    ):
                        continue
                    rows.append(row)
                if rows or not (flt or only_renamed):
                    mappings.append(
                        {
                            "provider": inp["name"],
                            "providerType": inp["providerType"],
                            "alias": inp["alias"],
                            "enteredAt": node_name,
                            "mappingCount": len(inp["mappings"]),
                            "assignments": rows,
                        }
                    )
        result["mappingTotal"] = total
        result["mappings"] = mappings

    if include_lineage:
        lineage = []
        unassigned = []
        out_elems = model.elements.get(model.output_node, [])
        # For a Union output node, report which direct part providers do not
        # feed a field (they deliver it empty). alias -> provider name.
        union_inputs: dict = {}
        if model.output_node in model.nodes and _hcpr_xsi(model.nodes[model.output_node]) == "Union":
            union_inputs = {
                (i["alias"] or i["name"]): i["name"]
                for i in model.inputs[model.output_node]
                if i["kind"] == "provider"
            }
        for el in out_elems:
            origins = model.trace_back(model.output_node, el["name"])
            if not origins:
                unassigned.append(el["name"])
            if flt:
                hay = [el["name"], el["infoObject"], el["label"]] + [
                    o.get("sourceField", "") for o in origins
                ] + [o.get("sourceFieldName", "") for o in origins]
                if not any(flt in (v or "").upper() for v in hay):
                    continue
            entry = {"name": el["name"]}
            entry.update(
                {k: v for k, v in el.items() if k not in ("name", "formula", "formulaFields", "calculated") and v}
            )
            entry["origins"] = origins
            if union_inputs and not el.get("calculated"):
                fed = {o.get("alias") for o in origins if "alias" in o}
                missing = [a for a in union_inputs if a not in fed]
                if missing:
                    entry["notFedBy"] = [union_inputs[a] for a in missing]
            lineage.append(entry)
        result["outputFieldCount"] = len(out_elems)
        result["lineage"] = lineage
        result["unassignedFields"] = unassigned

    return result


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
def get_characteristic_values(
    characteristic_name: str,
    info_provider: str = "",
    search_string: str = "",
    max_rows: int = 50,
    read_texts: bool = True,
    read_sids: bool = False,
    most_recent: bool = False,
) -> dict:
    """
    Read the actual master-data VALUES of a characteristic InfoObject.

    Unlike get_infoobject_details (which returns the InfoObject definition),
    this returns real data rows - the characteristic's values, optionally with
    their texts. Use it to answer "what are the values of 0CUSTOMER / 0MATERIAL
    / 0PLANT ...", either across the whole InfoObject's master data or
    restricted to the values that occur in a given InfoProvider.

    NOTE: This reads characteristic (master-data) values only. It does NOT run
    a BW query or return query result figures - the BW Modeling API on this
    system exposes no query-result runtime.

    Args:
        characteristic_name: Technical name of the characteristic (e.g.
            "0CUSTOMER", "0SOLD_TO", "0MATERIAL"). Navigation-attribute style
            names are resolved by the backend to their base characteristic.
        info_provider: Optional InfoProvider (e.g. "B080_V05") to restrict the
            values to those actually present in that provider. Leave empty to
            read the full master data of the InfoObject.
        search_string: Optional filter pattern applied to the values/texts.
        max_rows: Maximum number of value rows to return (default: 50).
        read_texts: If True (default), include the value texts/descriptions.
        read_sids: If True, also return the internal SIDs (default: False).
        most_recent: If True, prefer most-recently-used values (default: False).

    Returns:
        Dictionary with:
          - characteristic: the requested characteristic name
          - referenceCharacteristic: the base characteristic the backend
            resolved it to (e.g. 0SOLD_TO -> 0CUSTOMER), when reported
          - infoProvider: the InfoProvider filter used (if any)
          - columns: the value-help column names, in order (e.g. CHAVL_EXT =
            external/display value, CHAVL_INT = internal key, plus text columns)
          - rowCount: number of value rows returned
          - values: list of row dicts keyed by column name
    """
    conn = BWConnection.from_env()

    params: dict[str, str] = {
        "characteristicname": characteristic_name,
        "maxrows": str(max_rows),
        "readtexts": "true" if read_texts else "false",
        "readsids": "true" if read_sids else "false",
        "mostrecent": "true" if most_recent else "false",
    }
    if info_provider:
        params["infoprovider"] = info_provider
    if search_string:
        params["searchstring"] = search_string

    try:
        response = _bw_request(
            conn,
            "/sap/bw/modeling/is/values/characteristicvalues",
            params=params,
            accept="application/vnd.sap-bw-modeling.valuehelp2-v1_1_0+xml",
        )
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "detail": e.response.text[:500],
            "characteristic": characteristic_name,
        }

    rows = _parse_value_help(response.text)

    # Pull the reference characteristic from the meta information element, e.g.
    # <valueHelpMetaInformation referenceCharacteristic="0CUSTOMER" .../>.
    reference = ""
    try:
        root = ET.fromstring(response.text)
        for elem in root.iter():
            if elem.tag.split("}")[-1] == "valueHelpMetaInformation":
                reference = (elem.get("referenceCharacteristic") or "").strip()
                break
    except ET.ParseError:
        pass

    columns = list(rows[0].keys()) if rows else []

    return {
        "characteristic": characteristic_name,
        "referenceCharacteristic": reference,
        "infoProvider": info_provider,
        "columns": columns,
        "rowCount": len(rows),
        "values": rows,
    }


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


def _fetch_abap_program_source(conn: "BWConnection", program_name: str) -> str:
    """Fetch the full ABAP source of a program via the ADT REST service.

    BW generates the runtime for a transformation (start/end/expert routines
    and all field-level rule routines) into a single ABAP program, named in the
    transformation model's ``abapProgram`` attribute. That program's source is
    served by the ABAP Development Tools resource
    ``/sap/bc/adt/programs/programs/<name>/source/main`` (name lowercased,
    slashes percent-encoded).
    """
    enc = quote(program_name.lower(), safe="")
    url = f"{conn.base_url}/sap/bc/adt/programs/programs/{enc}/source/main"
    response = requests.get(
        url,
        auth=(conn.username, conn.password),
        headers={"Accept": "text/plain", "sap-client": conn.client},
        verify=conn.verify_ssl,
        timeout=60,
    )
    response.raise_for_status()
    return response.text


def _extract_abap_routines(program_source: str) -> list[dict]:
    """Extract the routine method bodies from a generated transformation program.

    In the generated program each transformation routine is an ABAP method:
      - ``compute_<rule>_<step>``  -> a field-level rule routine
      - ``invert_<rule>_<step>``   -> the corresponding inverse routine
      - ``start_routine`` / ``end_routine`` / ``expert_routine`` / ``global...``
    Only the customer-editable portion is meaningful; the generated wrapper is
    included so the reader sees the method signature comments (which document
    the source/target fields for the routine).

    Returns a list of dicts with the method name, the target field (when the
    generated comment exposes it), the 1-based line span in the program, and
    the full method text.
    """
    lines = program_source.splitlines()
    routines: list[dict] = []

    # Match method implementations whose names indicate a transformation routine.
    method_re = re.compile(
        r"^\s*method\s+"
        r"(compute_\w+|invert_\w+|\w*start_routine\w*|\w*end_routine\w*|"
        r"\w*expert_routine\w*|\w*global\w*)\s*\.",
        re.IGNORECASE,
    )
    endmethod_re = re.compile(r"^\s*endmethod\b", re.IGNORECASE)
    target_re = re.compile(r"target field:\s*(\S+)", re.IGNORECASE)

    idx = 0
    n = len(lines)
    while idx < n:
        m = method_re.match(lines[idx])
        if not m:
            idx += 1
            continue

        start = idx
        end = start
        for j in range(start, n):
            if endmethod_re.match(lines[j]):
                end = j
                break
        else:
            end = n - 1

        body = lines[start : end + 1]
        target = ""
        for ln in body:
            tm = target_re.search(ln)
            if tm:
                target = tm.group(1)
                break

        routines.append(
            {
                "method": m.group(1),
                "targetField": target,
                "startLine": start + 1,
                "endLine": end + 1,
                "code": "\n".join(body),
            }
        )
        idx = end + 1

    return routines


def _trfn_localname(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _trfn_step_type(step) -> str:
    """Return the readable step type of a rule <step> element.

    Prefers the explicit ``type`` attribute (DIRECT, CONSTANT, NO_UPDATE,
    ROUTINE, FORMULA, ...); falls back to the xsi:type local name
    (e.g. StepDirect -> Direct) when ``type`` is absent.
    """
    explicit = step.get("type", "")
    if explicit:
        return explicit
    xsi = step.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
    local = xsi.split(":")[-1] if ":" in xsi else xsi
    return local[4:] if local.startswith("Step") else local


def _elementref_field(container) -> str:
    """Extract the field name from a rule's <source>/<target> elementRef.

    The reference looks like ``#///target/segment1/0CUSTOMER``; the field is
    the last path segment. Returns "" when no elementRef is present.
    """
    if container is None:
        return ""
    for child in container:
        if _trfn_localname(child.tag) == "elementRef" and child.text:
            return child.text.rsplit("/", 1)[-1]
    return ""


def _parse_transformation_mappings(root, include_no_update: bool = False) -> list[dict]:
    """Parse the field-level mapping rules from a transformation model.

    Walks each rule group and returns one compact entry per rule:
      - target:    the target field (from the rule's target elementRef)
      - source:    the source field(s) (from source elementRefs), when present
      - stepType:  DIRECT / CONSTANT / NO_UPDATE / ROUTINE / FORMULA / ...
      - constant:  the constant value (only for CONSTANT steps)
      - group:     the rule group description (e.g. "Rules", "Technical Rules")

    NO_UPDATE rules (fields the transformation deliberately does not write) are
    omitted by default to keep the list focused on actual mappings; set
    ``include_no_update`` to include them.
    """
    mappings: list[dict] = []
    for group in root.iter():
        if _trfn_localname(group.tag) != "group":
            continue
        group_desc = group.get("description", "")
        for rule in group:
            if _trfn_localname(rule.tag) != "rule":
                continue

            target_field = ""
            source_fields = []
            for child in rule:
                lname = _trfn_localname(child.tag)
                if lname == "target":
                    target_field = _elementref_field(child)
                elif lname == "source":
                    src = _elementref_field(child)
                    if src:
                        source_fields.append(src)

            step = None
            for child in rule:
                if _trfn_localname(child.tag) == "step":
                    step = child
                    break
            step_type = _trfn_step_type(step) if step is not None else ""

            if step_type == "NO_UPDATE" and not include_no_update:
                continue

            entry: dict = {"target": target_field, "stepType": step_type}
            if source_fields:
                entry["source"] = (
                    source_fields[0] if len(source_fields) == 1 else source_fields
                )
            if step is not None and step.get("constant") is not None:
                entry["constant"] = step.get("constant")
            if group_desc:
                entry["group"] = group_desc
            mappings.append(entry)

    return mappings


@mcp.tool
def get_transformation_details(
    transformation_id: str,
    include_abap_code: bool = True,
    include_full_program: bool = False,
    include_raw_xml: bool = False,
    rule_filter: str = "",
    include_mappings: bool = True,
    include_no_update_mappings: bool = False,
) -> dict:
    """
    Get detailed metadata for a transformation including source, target,
    rule information, and (optionally) the ABAP routine source code.

    The transformation model itself only describes the rule structure. The
    actual ABAP for start/end/expert routines and field-level rule routines is
    generated into a single ABAP program (the model's ``abapProgram``). When
    ``include_abap_code`` is set, this tool also fetches that program's source
    via the ABAP Development Tools (ADT) service and extracts the individual
    routine methods (e.g. ``compute_<rule>_<step>`` for a field routine).

    Payloads can get large: the transformation model XML and the full generated
    program source are each substantial. By default only the parsed routine
    methods (``abapRoutines``) are returned - that is the useful part for
    reading routine logic. Use the flags below to opt into the bulky fields.

    Args:
        transformation_id: Technical ID of the transformation
            (e.g. "004BKVTJ2PRM1HA8G1Z7SB2E9VLVYK14").
        include_abap_code: If True (default), also retrieve the generated ABAP
            program name and the parsed routine methods. Set to False to
            return only the transformation model metadata.
        include_full_program: If True, also include the full generated ABAP
            program source under ``abapProgramSource``. Defaults to False to
            keep the response small; the parsed ``abapRoutines`` are usually
            enough. Only takes effect when ``include_abap_code`` is True.
        include_raw_xml: If True, include the full transformation model XML
            under ``rawXml``. Defaults to False to keep the response small.
        rule_filter: If set, narrows the response to the matching field
            (case-insensitive substring), on both the mappings and the ABAP
            routines:
              - ``mappings``: keeps entries whose target field or any source
                field contains the substring (e.g. "BUYGRP" returns just that
                field's mapping). ``mappingTotal`` still reports the full count.
              - ``abapRoutines``: keeps routines whose method name or target
                field matches, and reports ``abapRoutineCount`` (matched) and
                ``abapRoutineTotal`` (available).
            Use it for field-level lineage - e.g. "which field feeds BUYGRP".
            The mappings filter applies whenever include_mappings is on; the
            routine filter applies only when include_abap_code is on.
        include_mappings: If True (default), parse the field-level mapping
            rules from the model into a compact ``mappings`` list - one entry
            per rule with target field, source field(s), step type (DIRECT,
            CONSTANT, NO_UPDATE, ROUTINE, ...) and constant value where
            relevant. This is the cheap way to see the field mappings without
            dumping the whole model via include_raw_xml.
        include_no_update_mappings: If True, also include NO_UPDATE rules
            (fields the transformation deliberately does not write) in the
            ``mappings`` list. Defaults to False so the list focuses on actual
            mappings. Only takes effect when include_mappings is True.

    Returns:
        Dictionary with transformation details (source, target, rules). When
        include_mappings is on (default), adds:
          - mappingCount: number of mapping entries returned
          - mappingTotal: total rules in the model (before NO_UPDATE filtering)
          - mappings: compact per-rule mapping list (see include_mappings)
        When ABAP code is included, adds:
          - abapProgram: the generated program name
          - abapRoutines: list of routine methods, each with method name,
            target field (if known), line span, and code (filtered by
            rule_filter when provided)
          - abapProgramSource: the full generated program source (only when
            include_full_program is True)
        With include_raw_xml=True, also adds ``rawXml`` (the full model XML;
        redundant with mappings and much larger - avoid unless you need the raw
        model). If the ABAP retrieval fails, an ``abapCodeError`` entry
        explains why while the model metadata is still returned.
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
        }
        if include_raw_xml:
            details["rawXml"] = response.text

        # Try to extract basic info from the root element attributes
        for key, value in root.attrib.items():
            clean_key = key.split("}")[-1] if "}" in key else key
            details[clean_key] = value

        # Parse the compact field-level mapping list from the rule groups.
        if include_mappings:
            all_mappings = _parse_transformation_mappings(root, include_no_update=True)
            if include_no_update_mappings:
                mappings = all_mappings
            else:
                mappings = [m for m in all_mappings if m.get("stepType") != "NO_UPDATE"]
            details["mappingTotal"] = len(all_mappings)
            # rule_filter also narrows the mappings: keep entries whose target
            # or any source field contains the (case-insensitive) substring.
            if rule_filter:
                needle = rule_filter.lower()

                def _mapping_matches(m: dict) -> bool:
                    if needle in m.get("target", "").lower():
                        return True
                    src = m.get("source", "")
                    src_list = src if isinstance(src, list) else [src]
                    return any(needle in (s or "").lower() for s in src_list)

                mappings = [m for m in mappings if _mapping_matches(m)]
            details["mappingCount"] = len(mappings)
            details["mappings"] = mappings

    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "object": transformation_id,
        }

    # Optionally enrich with the generated ABAP routine source.
    if include_abap_code:
        program_name = details.get("abapProgram", "")
        if not program_name:
            details["abapCodeError"] = (
                "Transformation model has no abapProgram attribute; it may not "
                "contain any ABAP routines."
            )
        else:
            try:
                source = _fetch_abap_program_source(conn, program_name)
                routines = _extract_abap_routines(source)
                if rule_filter:
                    needle = rule_filter.lower()
                    matched = [
                        r
                        for r in routines
                        if needle in r.get("method", "").lower()
                        or needle in r.get("targetField", "").lower()
                    ]
                    details["abapRoutineTotal"] = len(routines)
                    details["abapRoutineCount"] = len(matched)
                    details["abapRoutines"] = matched
                else:
                    details["abapRoutines"] = routines
                if include_full_program:
                    details["abapProgramSource"] = source
            except requests.exceptions.HTTPError as e:
                details["abapCodeError"] = (
                    f"Could not fetch ABAP program '{program_name}' via ADT: "
                    f"HTTP {e.response.status_code}: {e.response.reason}"
                )
            except requests.exceptions.RequestException as e:
                details["abapCodeError"] = (
                    f"Could not fetch ABAP program '{program_name}' via ADT: {e}"
                )

    return details


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
        boundary = {
            "isVariable": True,
            "variableName": var.get("technicalName", "")
            or (value_child.text.strip() if value_child is not None and value_child.text else ""),
        }
        # Only emit description / value type when they carry information.
        if var.get("description"):
            boundary["variableDescription"] = var["description"]
        value_type = (
            type_child.text.strip() if type_child is not None and type_child.text else ""
        )
        if value_type:
            boundary["valueType"] = value_type
        return boundary

    # Literal boundary: prefer internalValue attr, else <value> child text
    internal = elem.get("internalValue")
    literal = internal
    if not literal and value_child is not None and value_child.text:
        literal = value_child.text.strip()
    if not literal:
        return {}
    return {"isVariable": False, "value": literal}


def _parse_range_token(token, variables: dict) -> dict:
    """Parse a SelectionRange token into a compact readable restriction dict.

    Low-value / empty fields are omitted to keep the payload small:
      - default operator "Equal" and default selectionType "range" are dropped
      - exclude is emitted only when true
      - empty descriptions and empty boundaries are dropped
      - a single-value equality range collapses to {"eq": <value>, ...}
        instead of separate from/to boundary objects
    """
    operator = token.get("operator", "")
    selection_type = token.get("selectionType", "")
    exclude = token.get("exclude", "false") == "true"
    from_desc = token.get("fromValueDesc", "")
    to_desc = token.get("toValueDesc", "")

    from_b = _parse_range_boundary(token.find("Qry:fromValue", QRY_NS), variables)
    to_b = _parse_range_boundary(token.find("Qry:toValue", QRY_NS), variables)

    result: dict = {"kind": "range"}

    # Collapse a plain single-value literal equality into a compact form.
    is_single_value = (
        operator in ("", "Equal")
        and from_b
        and not from_b.get("isVariable")
        and not to_b
    )
    if is_single_value:
        result["eq"] = from_b.get("value", "")
        if from_desc:
            result["desc"] = from_desc
    else:
        if operator and operator != "Equal":
            result["operator"] = operator
        if from_b:
            result["from"] = from_b
        if to_b:
            result["to"] = to_b
        if from_desc:
            result["fromValueDesc"] = from_desc
        if to_desc:
            result["toValueDesc"] = to_desc

    if exclude:
        result["exclude"] = True
    if selection_type and selection_type != "range":
        result["selectionType"] = selection_type

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
    result: dict = {
        "kind": "variable",
        "variableName": var.get("technicalName", ""),
    }
    operator = token.get("operator", "")
    if operator and operator != "Equal":
        result["operator"] = operator
    if token.get("exclude", "false") == "true":
        result["exclude"] = True
    selection_type = token.get("selectionType", "")
    if selection_type and selection_type != "variable":
        result["selectionType"] = selection_type
    if var.get("description"):
        result["variableDescription"] = var["description"]
    if var.get("procType"):
        result["variableProcType"] = var["procType"]
    return result


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
        entry = {
            "infoObject": group.get("infoObject", ""),
            "restrictions": restrictions,
        }
        group_desc = group.get("description")
        if group_desc:
            entry["description"] = group_desc
        groups.append(entry)
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
def get_query_filters(
    query_name: str,
    object_version: str = "A",
    section: str = "all",
    name_filter: str = "",
) -> dict:
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

    Both this tool and the underlying query model can be large. Use ``section``
    and ``name_filter`` to fetch only the part you need instead of everything.

    Args:
        query_name: Query technical name (e.g. "B080_V05_Q001").
        object_version: Object version, "A" (active, default) or "M" (modified).
        section: Which part(s) to return. One of:
            "all" (default) - filters + restricted key figures + structure
            members; "filters"; "restricted_key_figures" (alias "rkf");
            "structure_members". Sections not requested are omitted from the
            response (their count fields are omitted too).
        name_filter: Case-insensitive substring. When set, restricted key
            figures and structure members are limited to those whose technical
            name or description matches. Lets you fetch one RKF (e.g.
            "B080_V05_R001") instead of all of them. Does not filter the global
            ``filters`` list (those are keyed by InfoObject, returned as-is for
            the requested section).

    Returns:
        Dictionary with the query name, InfoProvider, and the requested
        section(s):
          - filters: the global query filter. Each entry has the InfoObject,
            usageType, filterType (BEx label), and its restrictions (fixed
            ranges with values, or resolved variables).
          - restrictedKeyFigures: reusable Restricted Key Figures (RKFs) with
            technicalName, description, and their per-characteristic
            restrictions.
          - structureMembers: local structure members (rows/columns) that
            carry selection restrictions, with description and restrictions.
            Pure formula members (no restriction) are omitted.
        When name_filter is set, ``restrictedKeyFigureTotal`` /
        ``structureMemberTotal`` report how many existed before filtering.
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

    # Normalise the requested section(s).
    section_norm = (section or "all").strip().lower()
    _section_aliases = {"rkf": "restricted_key_figures", "rkfs": "restricted_key_figures"}
    section_norm = _section_aliases.get(section_norm, section_norm)
    valid_sections = {"all", "filters", "restricted_key_figures", "structure_members"}
    if section_norm not in valid_sections:
        return {
            "error": (
                f"Invalid section '{section}'. Valid: filters, "
                "restricted_key_figures, structure_members, all."
            ),
            "query": query_name,
        }
    want_filters = section_norm in ("all", "filters")
    want_rkf = section_norm in ("all", "restricted_key_figures")
    want_members = section_norm in ("all", "structure_members")

    needle = name_filter.strip().lower()

    def _name_matches(*texts: str) -> bool:
        if not needle:
            return True
        return any(needle in (t or "").lower() for t in texts)

    variables = _build_variable_map(root)

    result: dict = {
        "query": query_elem.get("technicalName", query_name),
        "infoProvider": query_elem.get("providerName", ""),
        "objectVersion": version,
    }

    if want_filters:
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
        result["filterCount"] = len(filters)
        result["filters"] = filters

    if want_rkf:
        # Restricted key figures: reusable RestrictedMeasure components, each
        # carrying a MemberSelection with the characteristic restrictions.
        restricted_key_figures = []
        rkf_total = 0
        for elem in root.iter():
            if _xsi_type(elem) != "RestrictedMeasure":
                continue
            rkf_total += 1
            member = elem.find("Qry:member", QRY_NS)
            groups = (
                _parse_member_groups(member, variables) if member is not None else []
            )
            desc = _member_description(elem) or (
                elem.find("Qry:description", QRY_NS).get("value", "")
                if elem.find("Qry:description", QRY_NS) is not None
                else ""
            )
            tech_name = elem.get("technicalName", "")
            if not _name_matches(tech_name, desc):
                continue
            restricted_key_figures.append(
                {
                    "technicalName": tech_name,
                    "description": desc,
                    "reusable": elem.get("reusable", "false") == "true",
                    "restrictions": groups,
                }
            )
        result["restrictedKeyFigureCount"] = len(restricted_key_figures)
        if needle:
            result["restrictedKeyFigureTotal"] = rkf_total
        result["restrictedKeyFigures"] = restricted_key_figures

    if want_members:
        # Structure members: local (non-reusable) selection members defined
        # inside a structure/CustomDimension (rows or columns). Only report
        # members that carry actual selection restrictions (skip pure formulas/
        # empty members). Members belonging to a RestrictedMeasure are excluded
        # (reported as restricted key figures).
        parent_map = {c: p for p in root.iter() for c in p}
        structure_members = []
        member_total = 0
        for elem in root.iter():
            if _xsi_type(elem) != "MemberSelection":
                continue
            parent = parent_map.get(elem)
            if parent is not None and _xsi_type(parent) == "RestrictedMeasure":
                continue  # already covered as a restricted key figure
            groups = _parse_member_groups(elem, variables)
            if not groups:
                continue  # no real restriction (e.g. formula member)
            member_total += 1
            desc = _member_description(elem)
            if not _name_matches(desc):
                continue
            structure_members.append(
                {
                    "description": desc,
                    "restrictions": groups,
                }
            )
        result["structureMemberCount"] = len(structure_members)
        if needle:
            result["structureMemberTotal"] = member_total
        result["structureMembers"] = structure_members

    return result


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
            tech_name = member.get("technicalName", "")
            if mtype == "MemberSelection":
                measures.append(
                    {
                        "technicalName": tech_name,
                        "description": desc,
                        "measureType": "restricted",
                        "baseKeyFigure": _member_base_key_figure(member),
                    }
                )
            elif mtype == "MemberFormula":
                measures.append(
                    {
                        "technicalName": tech_name,
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
# Query data (BICS reporting) tool
# ---------------------------------------------------------------------------


def _bics_local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _parse_bics_axis(axis_elem) -> dict:
    """Parse a BICS <columns> or <rows> axis into headers + member tuples.

    An axis has:
      - <headers><entry .../></headers>: the dimensions on the axis, in order
        (characteristics, or a structure like "Key Figures"). Each entry's
        ``pos`` gives its column index within a tuple.
      - <tuples size=N><tuple><value .../></tuple>...: the members. Each
        <tuple> has one <value> per header entry (matched by the header id /
        order). A value carries extKey (external/display key), intKey (internal
        key), txt (text), and sid.

    Returns {"drillLevel", "headers": [...], "tuples": [[{...}, ...], ...]}.
    """
    if axis_elem is None:
        return {"headers": [], "tuples": []}

    headers = []
    tuples = []
    for child in axis_elem:
        tag = _bics_local(child.tag)
        if tag == "headers":
            for entry in child:
                if _bics_local(entry.tag) != "entry":
                    continue
                headers.append(
                    {
                        "name": entry.get("name", ""),
                        "text": entry.get("txt", ""),
                        "isStructure": entry.get("isStructure", "") == "true",
                        "isKeyFigures": entry.get("hasKeyfigures", "") == "true",
                    }
                )
        elif tag == "tuples":
            for tup in child:
                if _bics_local(tup.tag) != "tuple":
                    continue
                members = []
                for val in tup:
                    if _bics_local(val.tag) != "value":
                        continue
                    members.append(
                        {
                            "key": val.get("extKey", "") or val.get("intKey", ""),
                            "internalKey": val.get("intKey", ""),
                            "text": val.get("txt", ""),
                        }
                    )
                tuples.append(members)

    return {
        "drillLevel": axis_elem.get("drillLvl", ""),
        "headers": headers,
        "tuples": tuples,
    }


def _parse_bics_effective_selection(root) -> list[dict]:
    """Parse the <selection>/<effective> block: the filters actually applied.

    Each <infoObject name=..> holds one or more <selectValue> restrictions with
    sign (I/E include/exclude), op (EQ/BT/...), and low/high values.
    """
    effective = next(
        (e for e in root.iter() if _bics_local(e.tag) == "effective"), None
    )
    if effective is None:
        return []

    result = []
    for iobj in effective:
        if _bics_local(iobj.tag) != "infoObject":
            continue
        restrictions = []
        for sv in iobj:
            if _bics_local(sv.tag) != "selectValue":
                continue
            entry = {
                "sign": sv.get("sign", ""),
                "operator": sv.get("op", ""),
                "low": sv.get("low", "") or sv.get("lowInt", ""),
            }
            if sv.get("high") or sv.get("highInt"):
                entry["high"] = sv.get("high", "") or sv.get("highInt", "")
            restrictions.append(entry)
        result.append(
            {"infoObject": iobj.get("name", ""), "restrictions": restrictions}
        )
    return result


def _parse_bics_response(xml_text: str) -> dict:
    """Parse a BICS query-reporting response (<queryView>) into a clean result.

    The response executes the query in its default view and returns:
      - queryView attributes (name, text, uid, data rollup timestamp)
      - metaData (InfoProvider, whether the query has variables)
      - a <resultSet> with <columns> and <rows> axes and a sparse <data> block
        of <cell row=.. col=.. crv=.. txt=../> entries (crv = raw numeric value,
        txt = formatted display value). row/col are 1-based indexes into the
        respective axis tuple lists.
    """
    root = ET.fromstring(xml_text)

    result: dict = {
        "query": root.get("name", ""),
        "description": root.get("txt", ""),
        "uid": root.get("uid", ""),
        "dataRollup": root.get("dataRollup", ""),
        "isTransient": root.get("isTransient", "") == "true",
    }

    meta = next((e for e in root.iter() if _bics_local(e.tag) == "metaData"), None)
    if meta is not None:
        result["infoProvider"] = meta.get("infoProvider", "")
        result["infoProviderText"] = meta.get("infoProviderText", "")
        result["hasVariables"] = meta.get("hasVariables", "") == "true"

    result_set = next(
        (e for e in root.iter() if _bics_local(e.tag) == "resultSet"), None
    )
    if result_set is None:
        # No data grid was produced. The usual cause is that the query stopped
        # on its variable-entry screen because a mandatory variable needs a
        # value (variablesContainer/@inputRequired="true"). Surface the
        # variables so the caller understands what input is missing, rather
        # than returning a bare error.
        var_container = next(
            (e for e in root.iter() if _bics_local(e.tag) == "variablesContainer"),
            None,
        )
        variables = []
        input_required = False
        if var_container is not None:
            input_required = var_container.get("inputRequired", "") == "true"
            for var in var_container:
                if _bics_local(var.tag) != "variable":
                    continue
                variables.append(
                    {
                        "name": (var.get("altName") or var.get("name", "")).strip(),
                        "text": var.get("txt", ""),
                        "infoObject": var.get("iobj", ""),
                        "mandatory": var.get("mandatory", "") == "true",
                    }
                )
        mandatory = [v["name"] for v in variables if v["mandatory"]]
        result["hasResultSet"] = False
        result["inputRequired"] = input_required
        result["variables"] = variables
        result["mandatoryVariables"] = mandatory
        if input_required or mandatory:
            result["message"] = (
                "Query did not return data in its default view because it "
                "requires variable input. This tool runs the default view only "
                "and does not submit variable values."
                + (f" Mandatory variables: {', '.join(mandatory)}." if mandatory else "")
            )
        else:
            result["message"] = (
                "Query executed but returned no result set in its default view."
            )
        return result

    result["hasResultSet"] = True

    columns_elem = next(
        (c for c in result_set if _bics_local(c.tag) == "columns"), None
    )
    rows_elem = next(
        (c for c in result_set if _bics_local(c.tag) == "rows"), None
    )
    columns = _parse_bics_axis(columns_elem)
    rows = _parse_bics_axis(rows_elem)

    # Data cells: sparse list keyed by 1-based row/col into the axis tuples.
    cells = []
    data_elem = next(
        (c for c in result_set if _bics_local(c.tag) == "data"), None
    )
    if data_elem is not None:
        for cell in data_elem:
            if _bics_local(cell.tag) != "cell":
                continue
            raw = cell.get("crv", "")
            value = None
            if raw not in ("", None):
                try:
                    value = float(raw)
                except ValueError:
                    value = raw
            cells.append(
                {
                    "row": int(cell.get("row", "0") or 0),
                    "col": int(cell.get("col", "0") or 0),
                    "value": value,
                    "formatted": cell.get("txt", ""),
                }
            )

    result["columns"] = columns
    result["rows"] = rows
    result["rowTupleCount"] = len(rows["tuples"])
    result["columnTupleCount"] = len(columns["tuples"])
    result["cells"] = cells
    result["cellCount"] = len(cells)
    result["effectiveFilters"] = _parse_bics_effective_selection(root)
    return result


@mcp.tool
def read_query_data(query_name: str) -> dict:
    """
    Execute a BW Query and read its RESULT DATA (the numbers), not its design.

    This runs the query in its default initial view via the BW reporting
    (BICS) runtime and returns the resolved result: the row and column axis
    members (with keys and texts) and the data cells (raw numeric value plus
    formatted display value). This is the tool for "what are the actual figures
    in query X".

    HOW IT DIFFERS from get_query_structure / get_query_filters: those read the
    query DEFINITION (characteristics, key figures, measures, filters) from the
    design-time model. This tool EXECUTES the query and returns DATA.

    LIMITATIONS:
      - Returns the query's DEFAULT view: variables take their default values
        (no variable entry is supplied) and the drilldown is as the query is
        saved. Ad-hoc navigation (changing drilldown, passing variable values,
        filtering) is not supported by this tool.
      - If the query requires mandatory variable input, the default execution
        may return little or no data (hasResultSet: false, inputRequired:
        true). If that happens, or if you need a characteristic added to the
        drilldown that the query doesn't show by default, use
        read_query_data_drilldown instead - it submits variables (at their
        default values) and can add extra row characteristics, at the cost of
        being a slower, less battle-tested mechanism.
      - The cell list is sparse: only populated cells are returned, addressed
        by 1-based row/col indexes into the row/column tuple lists.

    Args:
        query_name: Query technical name (e.g. "B080_V05_Q005").

    Returns:
        Dictionary with:
          - query, description, uid, dataRollup (last data load timestamp)
          - infoProvider, infoProviderText, hasVariables
          - columns / rows: each with headers (axis dimensions) and tuples
            (the members; each member has key, internalKey, text)
          - rowTupleCount / columnTupleCount
          - cells: list of {row, col, value, formatted} (value = raw number,
            formatted = display string incl. unit/currency; row/col are 1-based
            indexes into rows.tuples / columns.tuples)
          - cellCount
          - effectiveFilters: the restrictions actually applied at runtime
            (e.g. fiscal year, fiscal variant), one entry per InfoObject
    """
    conn = BWConnection.from_env()

    try:
        response = _bw_request(
            conn,
            "/sap/bw/modeling/comp/reporting",
            params={"compid": query_name},
            accept="application/xml",
        )
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "detail": e.response.text[:500],
            "query": query_name,
        }

    return _parse_bics_response(response.text)


# ---------------------------------------------------------------------------
# InA (Information Access) protocol - ad-hoc query drilldown
#
# InA is the undocumented, stateful HTTP protocol behind BW/4HANA's own
# "Data Preview" Fiori app (/sap/bw/ina/GetResponse), reverse-engineered from
# that app's client-side source (sap.bw4.lib, served by the BSP app
# bw4_lib). Unlike the BICS endpoint above, it requires a persistent session
# (cookies + CSRF token) across a 3-call handshake: fetch metadata, submit
# variables, then request a result grid with a chosen row/column layout.
# ---------------------------------------------------------------------------

_INA_CLIENT_INFO = {"Version": "2019.02", "Identifier": "BW4QueryPreview", "Component": "BW4"}


def _ina_session(conn: BWConnection) -> tuple[requests.Session, str, list]:
    """Open an InA session and return (session, csrf_token, Analytics capabilities)."""
    session = requests.Session()
    session.auth = (conn.username, conn.password)
    session.params = {"sap-client": conn.client}
    session.verify = conn.verify_ssl

    response = session.get(
        f"{conn.base_url}/sap/bw/ina/GetServerInfo",
        headers={"X-CSRF-Token": "fetch"},
        timeout=30,
    )
    response.raise_for_status()
    token = response.headers.get("x-csrf-token", "")
    server_info = response.json()
    capabilities = next(
        (
            s["Capabilities"]
            for s in server_info.get("Services", [])
            if s.get("Service") == "Analytics"
        ),
        [],
    )
    return session, token, capabilities


def _ina_post(
    session: requests.Session, conn: BWConnection, token: str, body: dict
) -> dict:
    """POST one InA request body to GetResponse and return the parsed JSON."""
    response = session.post(
        f"{conn.base_url}/sap/bw/ina/GetResponse",
        json=body,
        headers={"X-CSRF-Token": token, "Content-Type": "application/json"},
        timeout=90,
    )
    response.raise_for_status()
    return response.json()


def _ina_default_field_name(dim: dict) -> str:
    """Pick the SetOperand.FieldName for a plain (non-variable) restriction
    on a dimension: date dimensions key off the plain key attribute,
    everything else prefers a DISPLAY_KEY(_NC) attribute if the dimension
    has one.
    """
    if dim.get("DimensionType") == 7:  # Date
        return dim["KeyAttribute"]
    names = dim["AttributeHierarchy"]["AttributeNames"]
    display_key_nc = dim["Name"] + ".DISPLAY_KEY_NC"
    display_key = dim["Name"] + ".DISPLAY_KEY"
    if display_key_nc in names:
        return display_key_nc
    if display_key in names:
        return display_key
    return dim["KeyAttribute"]


def _ina_field_name(var: dict, dim: dict) -> str:
    """Pick the SetOperand.FieldName for a variable's selection.

    Mirrors sap.bw4.lib.olap.Variable#serializeForSubmit: hierarchy
    variables key off the hierarchy attribute, everything else uses the
    same rule as a plain restriction (see _ina_default_field_name).
    """
    semantic_type = var.get("SemanticType")
    if semantic_type == "HierarchyNodeVariable":
        return dim["AttributeHierarchy"]["HierarchyKeyAttribute"]
    if semantic_type == "HierarchyNameVariable":
        return dim["KeyAttribute"]
    return _ina_default_field_name(dim)


def _ina_filter_subselection(name: str, values, dims_by_name: dict) -> dict:
    """Build one ad-hoc SubSelection entry restricting characteristic `name`
    to `values` (equality, or an IN-list when several values are given), in
    the same shape InA uses for FixedFilter/DynamicFilter and submitted
    Variables.
    """
    dim = dims_by_name.get(name)
    if dim is None:
        raise ValueError(f"'{name}' is not a characteristic of this query")
    if isinstance(values, (str, int, float)):
        values = [values]
    return {
        "SetOperand": {
            "FieldName": dim["KeyAttribute"],
            "Elements": [{"Low": str(v)} for v in values],
        }
    }


def _ina_serialize_variable(var: dict, dims_by_name: dict, override=None) -> dict:
    """Build one Variables[] entry. With no `override`, reuses the
    variable's current (default or already-submitted) value. With
    `override` (a value or list of values), submits that instead -
    equality, or an IN-list when several values are given.
    """
    dim = dims_by_name[var["DimensionReference"]["Name"]]
    values = copy.deepcopy(
        var.get("Values", {"Selection": {"SetOperand": {"Elements": []}}})
    )
    values["Selection"]["SetOperand"]["FieldName"] = _ina_field_name(var, dim)
    if override is not None:
        override_values = [override] if isinstance(override, (str, int, float)) else override
        values["Selection"]["SetOperand"]["Elements"] = [
            {"Low": str(v)} for v in override_values
        ]
    else:
        for element in values["Selection"]["SetOperand"].get("Elements", []):
            element.pop("operation", None)
            if element.get("Comparison") == "=":
                element.pop("Comparison", None)
    return {"Name": var["Name"], "Values": values}


def _ina_fetch_metadata(
    session: requests.Session,
    conn: BWConnection,
    token: str,
    capabilities: list,
    query_name: str,
) -> dict:
    """Step 1: fetch the full query Cube (dimensions, variables, filters)."""
    body = {
        "ClientInfo": _INA_CLIENT_INFO,
        "Metadata": {
            "Capabilities": capabilities,
            "Context": "Analytics",
            "DataSource": {"ObjectName": query_name, "Type": "Query"},
            "Expand": [
                "Axis?Name=Rows",
                "Axis?Name=Columns",
                "Axis?Name=Filter",
                "Axis?Name=Repository",
                "Variables",
                "VariableVariants",
                "QueryDataCells",
                "DynamicFilter",
                "Conditions",
                "Exceptions",
                "UniversalDisplayHierarchies",
            ],
            "Language": "EN",
        },
        "Options": ["StatefulServer"],
    }
    return _ina_post(session, conn, token, body)["Cube"]


def _ina_submit_variables(
    session: requests.Session,
    conn: BWConnection,
    token: str,
    capabilities: list,
    cube: dict,
    variable_values: dict | None = None,
) -> dict:
    """Step 2: confirm all variables, submitting each at its current value
    unless `variable_values` (variable technical name -> value or list of
    values) overrides it. The server otherwise refuses a result request
    with "Variables exist - enter values and submit". Returns the updated
    Cube.
    """
    dims_by_name = {d["Name"]: d for d in cube["Dimensions"]}
    if variable_values:
        known_names = {v["Name"] for v in cube["Variables"]}
        unknown = set(variable_values) - known_names
        if unknown:
            raise ValueError(
                f"Unknown variable(s) for this query: {', '.join(sorted(unknown))}. "
                f"Known variables: {', '.join(sorted(known_names))}"
            )
    definition_dims = [
        {
            "Attributes": [
                {"Name": a["Name"], "Obtainability": "Always"}
                for a in d.get("Attributes", [])
            ],
            "Axis": d.get("AxisDefault"),
            "Name": d["Name"],
            "NonEmpty": False,
            "ReadMode": d.get("ResultMode"),
            "ResultSetReadMode": d.get("ResultSetReadMode"),
            "ResultStructure": [
                {"Result": "Members", "Visibility": "Visible"},
                {"Result": "Total", "Visibility": "Visible"},
            ],
        }
        for d in cube["Dimensions"]
    ]
    body = {
        "Analytics": {
            "Capabilities": capabilities,
            "DataSource": {
                "InstanceId": cube["DataSource"]["InstanceId"],
                "ObjectName": cube["DataSource"]["ObjectName"],
                "Type": "Query",
            },
            "Definition": {
                "Dimensions": definition_dims,
                "Query": {
                    "Axes": [
                        {"Axis": "Columns", "ResultAlignment": "Bottom", "Type": 2, "ZeroSuppressionType": 0},
                        {"Axis": "Rows", "ResultAlignment": "Bottom", "Type": 1, "ZeroSuppressionType": 0},
                    ]
                },
                "QueryDataCells": cube.get("QueryDataCells", {}),
                "ResultSetFeatureRequest": {
                    "ResultEncoding": "None",
                    "ResultFormat": "Version2",
                    "SubSetDescription": {"ColumnFrom": 0, "ColumnTo": -1, "RowFrom": 0, "RowTo": -1},
                    "UseDefaultAttributeKey": False,
                },
                "Sort": [],
                "Variables": [
                    _ina_serialize_variable(
                        v, dims_by_name, (variable_values or {}).get(v["Name"])
                    )
                    for v in cube["Variables"]
                ],
            },
            "ProcessingDirectives": {"ProcessingStep": "VariableSubmit"},
        },
        "Language": "EN",
        "Options": ["StatefulServer"],
    }
    result = _ina_post(session, conn, token, body)
    errors = [m.get("Text", "") for m in result.get("Messages", []) if m.get("Type") == 2]
    if errors:
        raise RuntimeError("; ".join(errors))
    return result["Cube"]


def _ina_transform_structure_member(member: dict, dim_name: str) -> dict:
    """Convert a raw metadata structure member (keyed by compound field
    names) into the {Name, Description, Visibility} shape InA expects back.
    """
    return {
        "Name": member[f"{dim_name}.KEY"],
        "Description": member.get(f"{dim_name}.LONG_TEXT", ""),
        "Visibility": member.get("Visibility", "Visible"),
    }


def _ina_result_dimension(dim: dict) -> dict:
    """Build one Dimensions[] entry for a Rows/Columns dimension.

    Mirrors sap.bw4.lib.olap.Characteristic#serialize4ResultRequest.
    """
    entry = {
        "Attributes": (
            [
                {"Name": a["Name"], "Obtainability": "Always"}
                for a in dim.get("DefaultResultSetAttributes", [])
            ]
            or [{"Name": dim["KeyAttribute"], "Obtainability": "Always"}]
        ),
        "Axis": dim["AxisDefault"],
        "Description": dim.get("Description"),
        "KeyAttribute": dim.get("KeyAttribute"),
        "Name": dim["Name"],
        "NonEmtpy": False,
        "ReadMode": dim.get("VariableReadMode"),
        "ResultSetReadMode": dim.get("ResultSetReadMode"),
        "ResultStructure": dim.get("DefaultResultStructure")
        or [
            {"Result": "Members", "Visibility": "Visible"},
            {"Result": "Total", "Visibility": "Visible"},
        ],
    }
    if dim.get("Members"):
        entry["Members"] = [
            _ina_transform_structure_member(m, dim["Name"])
            for m in dim["Members"]
            if m.get("Visibility", "Visible") == "Visible"
        ]
    return entry


def _ina_fetch_result(
    session: requests.Session,
    conn: BWConnection,
    token: str,
    capabilities: list,
    cube: dict,
    extra_row_dims: list[str],
    row_limit: int,
    column_limit: int,
    filters: dict | None = None,
) -> dict:
    """Step 3: request the result grid, with extra_row_dims added to the row
    axis ahead of the query's own Rows/Columns dimensions, and `filters`
    (characteristic name -> value or list of values) applied as additional
    ad-hoc restrictions alongside the query's design-time filters.
    """
    dims_by_name = {d["Name"]: d for d in cube["Dimensions"]}
    rowcol_dims = [d for d in cube["Dimensions"] if d.get("AxisDefault") in ("Rows", "Columns")]

    extra_dims = []
    for name in extra_row_dims:
        if name not in dims_by_name:
            raise ValueError(f"'{name}' is not a characteristic of this query")
        extra = copy.deepcopy(dims_by_name[name])
        extra["AxisDefault"] = "Rows"
        extra_dims.append(extra)

    # The query's own design-time filters (FixedFilter, e.g. 0FISCVARNT=V5,
    # and DynamicFilter, e.g. which restricted key figures are active) are
    # NOT applied automatically by this step - they must be echoed back here
    # or the server aggregates over the unrestricted data/every key figure,
    # which is dramatically more expensive and can time the request out.
    design_time_subselections = []
    for filter_key in ("FixedFilter", "DynamicFilter"):
        design_filter = cube.get(filter_key) or {}
        design_time_subselections.extend(
            design_filter.get("Selection", {}).get("Operator", {}).get("SubSelections", [])
        )

    for name, values in (filters or {}).items():
        design_time_subselections.append(
            _ina_filter_subselection(name, values, dims_by_name)
        )

    body = {
        "ClientInfo": _INA_CLIENT_INFO,
        "Analytics": {
            "Capabilities": capabilities,
            "DataSource": {
                "InstanceId": cube["DataSource"]["InstanceId"],
                "ObjectName": cube["DataSource"]["ObjectName"],
                "Type": "Query",
            },
            "Definition": {
                "Filter": {"Selection": {"Operator": {"Code": "And", "SubSelections": design_time_subselections}}},
                "Conditions": [],
                "Exceptions": [],
                "Sort": [],
                "Dimensions": [_ina_result_dimension(d) for d in extra_dims + rowcol_dims],
                "Variables": [_ina_serialize_variable(v, dims_by_name) for v in cube["Variables"]],
                "Name": cube["DataSource"]["ObjectName"],
                "InputEnabled": True,
                "Query": {"Axes": cube["Query"]["Axes"]},
                "CurrencyTranslation": {"targetCurrency": "", "translation": "", "text": ""},
                "QueryDataCells": cube.get("QueryDataCells", {}),
                "ResultSetFeatureRequest": {
                    "ResultEncoding": "None",
                    "ResultFormat": "Version2",
                    "SubSetDescription": {
                        "ColumnFrom": 0,
                        "ColumnTo": column_limit,
                        "RowFrom": 0,
                        "RowTo": row_limit,
                    },
                    "UseDefaultAttributeKey": False,
                },
                "UniversalDisplayHierarchies": cube.get("UniversalDisplayHierarchies"),
            },
        },
        "Options": ["StatefulServer"],
    }
    return _ina_post(session, conn, token, body)


def _ina_dimension_members(dim: dict) -> list[dict]:
    """Expand one axis dimension's columnar Attributes[].Values into a list
    of {key, internalKey, text} members, one per member index.

    InA encodes members column-wise: each Attribute (".KEY", ".DISPLAY_KEY",
    ".MEDIUM_TEXT", ...) carries its own parallel Values array; a member's
    row is the same index across all of a dimension's attributes.
    """
    attrs = {a["Name"].rsplit(".", 1)[-1]: a.get("Values", []) for a in dim.get("Attributes", [])}
    count = dim.get("MemberTypes", {}).get("Size") or len(next(iter(attrs.values()), []))
    members = []
    for i in range(count):
        internal_key = attrs["KEY"][i] if "KEY" in attrs and i < len(attrs["KEY"]) else None
        key = None
        for pref in ("DISPLAY_KEY", "KEY"):
            if pref in attrs and i < len(attrs[pref]):
                key = attrs[pref][i]
                break
        text = None
        for pref in ("MEDIUM_TEXT", "LONG_TEXT", "SHORT_TEXT"):
            if pref in attrs and i < len(attrs[pref]):
                text = attrs[pref][i]
                break
        members.append({"key": key, "internalKey": internal_key, "text": text or ""})
    return members


def _ina_parse_axis(axis: dict) -> dict:
    """Parse one Grids[0].Axes[] entry into {headers, tuples}, matching the
    shape read_query_data returns for columns/rows.
    """
    dims = axis.get("Dimensions", [])
    headers = [
        {
            "name": d["Name"],
            "text": d.get("Description", ""),
            "isStructure": d.get("DimensionType") == 2,
        }
        for d in dims
    ]
    dim_members = [_ina_dimension_members(d) for d in dims]
    tuple_count = axis.get("TupleCount", 0)
    per_dim_indexes = [t.get("MemberIndexes", {}).get("Values", []) for t in axis.get("Tuples", [])]

    tuples = []
    for t in range(tuple_count):
        entry = []
        for d, indexes in zip(dim_members, per_dim_indexes):
            idx = indexes[t] if t < len(indexes) else -1
            entry.append(d[idx] if 0 <= idx < len(d) else {"key": "", "internalKey": "", "text": ""})
        tuples.append(entry)

    return {"headers": headers, "tuples": tuples}


def _ina_parse_cells(grid: dict) -> list[dict]:
    """Flatten Grids[0].Cells into the {row, col, value, formatted} shape
    read_query_data uses (1-based, row-major: columns vary fastest).
    """
    columns_axis = next((a for a in grid.get("Axes", []) if a.get("Type") == "Columns"), {})
    col_count = columns_axis.get("TupleCount", 0) or 1
    cells_data = grid.get("Cells", {})
    values = cells_data.get("Values", {}).get("Values", [])
    formatted = cells_data.get("ValuesFormatted", {}).get("Values", [])

    cells = []
    for i, value in enumerate(values):
        cells.append(
            {
                "row": i // col_count + 1,
                "col": i % col_count + 1,
                "value": value,
                "formatted": formatted[i] if i < len(formatted) else "",
            }
        )
    return cells


def _ina_parse_grid(grid: dict) -> dict:
    """Parse one Grids[0] entry into the same columns/rows/cells shape
    read_query_data returns, for consistency between the two tools.
    """
    axes = {a.get("Type"): a for a in grid.get("Axes", [])}
    columns = _ina_parse_axis(axes.get("Columns", {}))
    rows = _ina_parse_axis(axes.get("Rows", {}))
    return {
        "description": grid.get("DataSource", {}).get("Description", ""),
        "columns": columns,
        "rows": rows,
        "rowTupleCount": axes.get("Rows", {}).get("TupleCount", 0),
        "columnTupleCount": axes.get("Columns", {}).get("TupleCount", 0),
        "cells": _ina_parse_cells(grid),
        "cellCount": len(grid.get("Cells", {}).get("Values", {}).get("Values", [])),
    }


@mcp.tool
def read_query_data_drilldown(
    query_name: str,
    drilldown: list[str],
    filters: dict | None = None,
    variable_values: dict | None = None,
    row_limit: int = 200,
    column_limit: int = 50,
) -> dict:
    """
    Execute a BW Query with an AD-HOC DRILLDOWN not present in its saved
    default view (e.g. add "Customer: CPV Band" to a Sales History query
    that doesn't have it on rows/columns by default), optionally restricted
    by an AD-HOC FILTER on one or more characteristics and/or overriding
    one or more of the query's VARIABLE values.

    read_query_data always returns the query's saved default view - it has
    no way to add a characteristic that the query designer left on the
    "free" axis (available, but not currently drilled into), no way to
    restrict the result to specific characteristic values before execution,
    and no way to submit a variable value other than the query's default.
    This tool fills those gaps: it adds the given characteristic(s) to the
    row axis, restricts the result to the given filter values, and/or
    submits the given variable overrides, in addition to the query's
    existing row/column characteristics, design-time filters, and other
    variables (submitted at their default values), then executes the query
    and returns the resulting grid.

    HOW THIS WORKS: BW/4HANA's own "Data Preview" Fiori app talks to an
    internal, undocumented HTTP API called InA (Information Access) at
    /sap/bw/ina/GetResponse. This tool replicates that protocol in three
    calls: fetch the query's full metadata, submit its variables (at their
    default value, or the given `variable_values` override), then request a
    result grid with the extra drilldown and filter restrictions applied.
    The filter is pushed down server-side (via the same Filter/SubSelections
    mechanism InA uses to re-apply the query's own design-time filters) -
    it is not a client-side post-filter of the full result. Validated end
    to end, including a two-level nested row drilldown.

    `filters` vs. `variable_values` - use the right one:
      - A characteristic can be restricted by BOTH a query variable (which
        this tool otherwise submits at its default) AND an ad-hoc `filters`
        entry at the same time - they combine with AND. If the variable's
        default is narrower than what you want (e.g. a "current fiscal
        year" variable), a `filters` restriction outside that default
        range silently intersects to nothing (empty result, no error) -
        use `variable_values` to change the variable's own value instead.
      - Check get_query_filters first: a "Default Value" restriction on a
        characteristic is driven by a variable - override the variable
        (via `variable_values`, using its variable name, e.g.
        "0CALDAY_CEX_SV_MAND_INP02") if you need a value outside that
        variable's default. A "Fixed Filter" is baked into the query
        design itself and cannot be overridden by either mechanism.

    LIMITATIONS:
      - `filters`/`variable_values` values are matched by equality (or
        membership, for a list of values) against the characteristic's
        internal key attribute - for a compounded characteristic (e.g.
        0FISCYEAR, compounded on 0FISCVARNT), that means the full compound
        key (e.g. "V52022", not "2022"); for a date variable, BW's internal
        YYYYMMDD format (e.g. "20230101" for 1 Jan 2023). No ranges,
        exclusions, or comparison operators (">", "<=").
      - A variable driven by a customer-exit/SAP-exit routine (common for
        "current date"-style variables) may recompute its own value from
        that routine's logic regardless of what's submitted, depending on
        how the exit is implemented - `variable_values` cannot guarantee
        an override in that case.
      - This is an undocumented, reverse-engineered protocol (no SAP
        documentation backs it, unlike the BW Modeling API read_query_data
        uses), so treat it as less battle-tested. The result-fetch step
        must echo back the query's own design-time filters (FixedFilter /
        DynamicFilter - e.g. which restricted/calculated key figures are
        active) or the server ends up aggregating far more than the query
        actually needs, which can time the request out even for a tiny
        row_limit; this tool already does that automatically, but a query
        with filter constructs not covered by that logic could still hit
        this.
      - You can only drill down on or filter a characteristic that is
        already part of the query's design (any axis, including "free");
        check get_query_structure for what's available. You cannot
        introduce an InfoObject the query doesn't reference at all.

    Args:
        query_name: Query technical name.
        drilldown: Characteristic technical name(s) to add to the row axis,
            nested in the given order (e.g. ["0SOLD_TO__0CUST_CLASS"]).
        filters: Optional ad-hoc restrictions applied before execution, as
            {characteristic_name: value} or {characteristic_name: [value,
            ...]} (e.g. {"0CUSTOMER": "0003021808", "0FISCPER": ["2024001",
            "2024002"]}). Combined with AND across characteristics; values
            for the same characteristic are combined with OR (an IN-list).
        variable_values: Optional overrides for the query's own variables
            (see get_query_filters/read_query_data for variable names), as
            {variable_name: value} or {variable_name: [value, ...]} (e.g.
            {"0CALDAY_CEX_SV_MAND_INP02": "20230101"}). Variables not
            listed here are submitted at their default value, as usual.
        row_limit: Max rows to request (default 200).
        column_limit: Max columns to request (default 50).

    Returns:
        Same shape as read_query_data (description, columns/rows with
        headers+tuples, rowTupleCount/columnTupleCount, cells as
        {row, col, value, formatted}, cellCount), plus "drilldown" and
        "messages". On failure: {"error", "query", ...}.
    """
    conn = BWConnection.from_env()

    try:
        session, token, capabilities = _ina_session(conn)
        cube = _ina_fetch_metadata(session, conn, token, capabilities, query_name)
        cube = _ina_submit_variables(
            session, conn, token, capabilities, cube, variable_values
        )
        result = _ina_fetch_result(
            session,
            conn,
            token,
            capabilities,
            cube,
            drilldown,
            row_limit,
            column_limit,
            filters,
        )
    except requests.exceptions.Timeout:
        return {
            "error": "Request timed out while fetching the result grid.",
            "query": query_name,
            "note": (
                "This can happen on large/wide InfoProviders - the InA "
                "result-fetch step appears to be data/complexity dependent, "
                "not necessarily caused by the drilldown itself. Try a "
                "smaller row_limit/column_limit or a lighter query."
            ),
        }
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "detail": e.response.text[:500],
            "query": query_name,
        }
    except (ValueError, RuntimeError, KeyError) as e:
        return {"error": str(e), "query": query_name}

    messages = [m.get("Text", "") for m in result.get("Messages", []) if m.get("Text")]
    if any(m.get("Type") == 2 for m in result.get("Messages", [])):
        return {"error": "; ".join(messages), "query": query_name, "raw": result}

    grids = result.get("Grids") or []
    if not grids:
        return {"error": "No result grid returned.", "query": query_name, "raw": result}

    parsed = _ina_parse_grid(grids[0])
    return {
        "query": query_name,
        "drilldown": drilldown,
        "filters": filters or {},
        "variableValues": variable_values or {},
        "messages": messages,
        **parsed,
    }


# ---------------------------------------------------------------------------
# ADT Data Preview - freestyle Open SQL
#
# This is the same service ABAP Development Tools (Eclipse) uses for its
# "Open SQL Console" / freestyle data preview, and what the abap-adt-api
# library's runQuery() wraps (see mcp-abap-abap-adt-api's QueryHandlers.ts
# and node_modules/abap-adt-api/build/api/tablecontents.js). Unlike the InA
# protocol above, this IS a documented-ish, stable part of ADT tooling - it
# just needs a CSRF handshake since it's a POST.
# ---------------------------------------------------------------------------

_ADT_INT_TYPES = {"I", "b", "8", "s"}
_ADT_FLOAT_TYPES = {"/", "a", "e", "F", "N", "%", "P"}
_ADT_DATE_TYPE = "D"


def _adt_local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _adt_attrs(elem) -> dict:
    return {_adt_local(k): v for k, v in elem.attrib.items()}


def _adt_csrf_session(conn: BWConnection) -> tuple[requests.Session, str]:
    """Open a session and fetch a real CSRF token via a small ADT endpoint
    (the compatibility graph, ~30KB) - freestyle/ddic data preview is a POST
    and rejects "fetch" outright rather than returning a token for it.
    """
    session = requests.Session()
    session.auth = (conn.username, conn.password)
    session.params = {"sap-client": conn.client}
    session.verify = conn.verify_ssl

    response = session.get(
        f"{conn.base_url}/sap/bc/adt/compatibility/graph",
        headers={"X-CSRF-Token": "fetch"},
        timeout=30,
    )
    response.raise_for_status()
    return session, response.headers.get("x-csrf-token", "")


def _adt_decode_value(type_code: str, raw: str):
    """Convert one cell's raw string value per its ABAP type code, mirroring
    abap-adt-api's decodeQueryResult/parseValue.
    """
    if raw == "":
        return None if type_code in _ADT_INT_TYPES or type_code in _ADT_FLOAT_TYPES else raw
    if type_code == _ADT_DATE_TYPE and len(raw) >= 8 and raw[:8].isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    if type_code in _ADT_FLOAT_TYPES:
        try:
            return float(raw)
        except ValueError:
            return raw
    if type_code in _ADT_INT_TYPES:
        try:
            return int(raw)
        except ValueError:
            return raw
    return raw


def _parse_datapreview_response(xml_text: str, decode: bool) -> dict:
    """Parse a dataPreview:tableData response (freestyle or ddic) into
    {columns, values, rowCount, totalRows, executedQuery,
    queryExecutionTimeSeconds}.
    """
    root = ET.fromstring(xml_text)

    def _child_text(name: str) -> str | None:
        elem = next((e for e in root if _adt_local(e.tag) == name), None)
        return elem.text if elem is not None else None

    columns_meta = []
    column_data = []  # (name, type_code, [raw values])
    for col_elem in root:
        if _adt_local(col_elem.tag) != "columns":
            continue
        metadata_elem = next((c for c in col_elem if _adt_local(c.tag) == "metadata"), None)
        dataset_elem = next((c for c in col_elem if _adt_local(c.tag) == "dataSet"), None)
        attrs = _adt_attrs(metadata_elem) if metadata_elem is not None else {}
        values = [d.text or "" for d in dataset_elem] if dataset_elem is not None else []
        name = attrs.get("name", "")
        type_code = attrs.get("type", "")
        columns_meta.append(
            {
                "name": name,
                "type": type_code,
                "description": attrs.get("description", ""),
                "keyAttribute": attrs.get("keyAttribute") == "true",
                "colType": attrs.get("colType", ""),
                "isKeyFigure": attrs.get("isKeyFigure") == "true",
            }
        )
        column_data.append((name, type_code, values))

    row_count = max((len(values) for _, _, values in column_data), default=0)
    rows = []
    for i in range(row_count):
        row = {}
        for name, type_code, values in column_data:
            raw = values[i] if i < len(values) else ""
            row[name] = _adt_decode_value(type_code, raw) if decode else raw
        rows.append(row)

    total_rows_text = _child_text("totalRows")
    exec_time_text = _child_text("queryExecutionTime")

    return {
        "columns": columns_meta,
        "values": rows,
        "rowCount": len(rows),
        "totalRows": int(total_rows_text) if total_rows_text and total_rows_text.isdigit() else len(rows),
        "executedQuery": _child_text("executedQueryString") or "",
        "queryExecutionTimeSeconds": float(exec_time_text) if exec_time_text else None,
    }


@mcp.tool
def run_sql_query(sql_query: str, row_number: int = 100, decode: bool = True) -> dict:
    """
    Run a read-only ABAP Open SQL query against the BW system's database
    tables/views and return the result rows.

    This is for reading raw tables, generated ADSO/CompositeProvider tables,
    or DDIC views directly with SQL - e.g. an InfoObject's master-data
    attribute table ("SELECT customer, 0cust_class FROM /bi0/pcustomer
    WHERE customer = '0003021808'"), or any other ABAP-visible table. It
    uses the same ADT "Data Preview" service that powers Eclipse's Open SQL
    Console (and is what the separate bw-adt-* MCP server's runQuery tool
    wraps too) - so it's ABAP Open SQL, not native HANA SQL: no "FROM
    DUMMY", joins/functions are whatever Open SQL supports on this system.

    HOW THIS DIFFERS from read_query_data / read_query_data_drilldown: those
    execute a BW QUERY - its OLAP logic (variables, restricted/calculated
    key figures, currency translation, exception aggregation) all get
    applied. This tool runs a plain SQL SELECT against the underlying
    tables - no query logic is applied at all, so it's the right tool when
    you need to look up a specific record (e.g. one customer's master-data
    attribute) rather than execute a query's design.

    READING FROM AN ADSO (Advanced DataStore Object): each ADSO physically
    generates several /BIC/A<name><suffix> tables - 1=inbound (staged, not
    yet activated), 2=active (activated data), 3=change log, plus some
    generated view tables. Querying only the active table (suffix 2) misses
    records that are loaded but not yet activated. Suffix 7 is a generated
    view unioning inbound + active, so prefer it (e.g. "/bic/ab020_d037")
    when you want the ADSO's full current-state data regardless of
    activation status. Confirm the exact table for a given ADSO first (its
    suffix scheme can vary by ADSO type) with something like:
    "SELECT tabname, ddtext FROM dd02t WHERE tabname LIKE '%<adso_name>%'
    AND ddlanguage = 'E'".

    LIMITATIONS:
      - Read-only; write statements are not meaningful here and not
        supported by this tool's use case.
      - row_number caps how many rows come back (like an implicit "UP TO n
        ROWS"); totalRows in the response tells you if more existed.
      - Requires the same authorizations as the ABAP Open SQL Console in
        Eclipse - a locked-down system may reject this even though the user
        can browse tables via other means.

    Args:
        sql_query: ABAP Open SQL SELECT statement (e.g. "SELECT mandt,
            customer, name1 FROM /bi0/pcustomer WHERE customer =
            '0003021808'"). No trailing period needed.
        row_number: Max rows to fetch (default 100).
        decode: If True (default), convert numeric/date column values to
            native JSON types (int/float, ISO date string) instead of raw
            ABAP strings.

    Returns:
        Dictionary with:
          - columns: list of {name, type, description, keyAttribute,
            colType, isKeyFigure} (type is the ABAP kind code, e.g. "C"=char,
            "P"=packed, "D"=date, "I"=int)
          - values: list of row dicts keyed by column name
          - rowCount: number of rows returned (<= row_number)
          - totalRows: total rows the query matched (may exceed rowCount)
          - executedQuery: the actual ABAP statement the server ran
          - queryExecutionTimeSeconds
    """
    conn = BWConnection.from_env()

    try:
        session, token = _adt_csrf_session(conn)
        response = session.post(
            f"{conn.base_url}/sap/bc/adt/datapreview/freestyle",
            params={"rowNumber": row_number},
            headers={
                "Accept": "application/*",
                "Content-Type": "text/plain",
                "X-CSRF-Token": token,
            },
            data=sql_query.encode("utf-8"),
            timeout=90,
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "detail": e.response.text[:500],
            "sqlQuery": sql_query,
        }

    result = _parse_datapreview_response(response.text, decode)
    return {"sqlQuery": sql_query, **result}


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

    # Process-chain usage is exposed directly in the DTP model, under
    # <usageInProcessChains><processChain name=.. description=.. tlogo="RSPC"/>.
    process_chains = []
    seen_chains = set()
    for usage in root.iter():
        if _local(usage.tag) != "usageInProcessChains":
            continue
        for pc in list(usage):
            if _local(pc.tag) != "processChain":
                continue
            name = pc.get("name", "")
            if name and name not in seen_chains:
                seen_chains.add(name)
                process_chains.append(
                    {"name": name, "description": pc.get("description", "")}
                )
    result["processChains"] = process_chains

    return result


# ---------------------------------------------------------------------------
# DTP details tool
# ---------------------------------------------------------------------------

# DTP extraction mode codes (extractionSettings/@extractionMode).
_DTP_EXTRACTION_MODES = {
    "F": "Full",
    "D": "Delta",
    "I": "Initialization (non-cumulative)",
    "N": "No transfer (init without data)",
}

# DTP processing mode codes (execution/@processingMode).
_DTP_PROCESSING_MODES = {
    "E": "Serial extraction, immediate parallel processing (background)",
    "P": "Parallel extraction and processing",
    "S": "Serial in dialog (debug)",
    "N": "No data transfer",
}


def _dtp_local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _parse_dtp_filter_field(field_elem) -> dict:
    """Parse one <fields> element of a DTP filter into a compact restriction.

    A filtered field carries filterSelection="X" and either:
      - one or more <selection> children (fixed value/range restriction).
        Each <selection> has an operator (Equal/Between/...) and an
        excluding flag (true -> Exclude/E, false -> Include/I), with a <low>
        value and optional <high> value; or
      - a <routine> child (ABAP routine restriction), holding the routine
        source as a sequence of <code> lines instead of fixed selections.
    The <operators> children are UI metadata (the list of available
    operators) and are ignored.
    """
    selections = []
    for sel in field_elem:
        if _dtp_local(sel.tag) != "selection":
            continue
        low_elem = next((c for c in sel if _dtp_local(c.tag) == "low"), None)
        high_elem = next((c for c in sel if _dtp_local(c.tag) == "high"), None)
        excluding = sel.get("excluding", "false") == "true"
        entry: dict = {
            "sign": "E" if excluding else "I",
            "operator": sel.get("operator", ""),
        }
        if low_elem is not None and low_elem.get("value") is not None:
            entry["low"] = low_elem.get("value")
        if high_elem is not None and high_elem.get("value") is not None:
            entry["high"] = high_elem.get("value")
        selections.append(entry)

    result: dict = {
        "field": field_elem.get("name", ""),
        "dtaName": field_elem.get("dtaName", ""),
        "description": field_elem.get("description", ""),
        "selections": selections,
    }

    routine_elem = next(
        (c for c in field_elem if _dtp_local(c.tag) == "routine"), None
    )
    if routine_elem is not None:
        code_lines = [
            (c.text or "") for c in routine_elem if _dtp_local(c.tag) == "code"
        ]
        result["filterType"] = "routine"
        result["abapCode"] = "\n".join(code_lines)
    elif selections:
        result["filterType"] = "selection"

    return result


def _parse_dtp_filters(root) -> list[dict]:
    """Return the DTP's filter restrictions - one entry per filtered field.

    Only fields that actually carry a restriction - fixed <selection>(s) or
    an ABAP <routine> - are returned (fields present in the model purely as
    filterable candidates are skipped).
    """
    filt = next((e for e in root.iter() if _dtp_local(e.tag) == "filter"), None)
    if filt is None:
        return []
    filters = []
    for field_elem in filt:
        if _dtp_local(field_elem.tag) != "fields":
            continue
        has_restriction = any(
            _dtp_local(c.tag) in ("selection", "routine") for c in field_elem
        )
        if not has_restriction:
            continue
        filters.append(_parse_dtp_filter_field(field_elem))
    return filters


def _dtp_endpoint_summary(elem) -> dict:
    """Compact {name, type, description} for a DTP <source>/<target>."""
    if elem is None:
        return {}
    attrs = {_dtp_local(k): v for k, v in elem.attrib.items()}
    return {
        "name": (attrs.get("name") or "").strip(),
        "type": attrs.get("tlogo", "") or attrs.get("type", ""),
        "description": attrs.get("description", ""),
    }


@mcp.tool
def get_dtp_details(
    dtp_id: str, section: str = "all", name_filter: str = ""
) -> dict:
    """
    Get details for a Data Transfer Process (DTP): its source/target,
    extraction and processing settings, and (most usefully) its filter
    restrictions.

    Reads the DTP model (dtpa) from the BW Modeling API. A DTP moves data from
    a source InfoProvider/DataSource to a target via a transformation, and can
    carry a filter that restricts which records are transferred. This tool
    exposes those filters, which no other tool currently returns.

    Args:
        dtp_id: DTP technical name (e.g. "DTP_5KC6P8RN2WFAV1XITY14CNAF7").
        section: Which part(s) to return. One of:
            "all" (default) - info + settings + filters;
            "info" - just source/target and description;
            "settings" - extraction/processing/error-handling settings;
            "filters" - just the filter restrictions.
            Sections not requested are omitted (their count fields too).
        name_filter: Case-insensitive substring. When set, the filters list is
            limited to fields whose name or description matches (e.g.
            "PLAN_ID"). Lets you fetch one field's restriction instead of all.
            ``filterCount`` reflects the match; ``filterTotal`` reports how many
            filtered fields exist.

    Returns:
        Dictionary with the DTP name and the requested section(s):
          - source / target: {name, type, description}
          - description: the DTP description
          - settings: extractionMode (+ label), allowedExtractionModes,
            packageSize, parallelExtraction, processingMode (+ label),
            errorHandling (requestHandling, numberOfErrorsPerPackage)
          - filters: one entry per filtered field, each with field, dtaName,
            description, filterType (selection/routine), and either
            selections (each: sign I/E, operator, low, high?) for a fixed
            restriction or abapCode (the routine source) for an ABAP-routine
            restriction
          - filterCount / filterTotal (when filters are included)
    """
    section_norm = (section or "all").strip().lower()
    valid_sections = {"all", "info", "settings", "filters"}
    if section_norm not in valid_sections:
        return {
            "error": (
                f"Invalid section '{section}'. Valid: all, info, settings, "
                "filters."
            ),
            "dtp": dtp_id,
        }
    want_info = section_norm in ("all", "info")
    want_settings = section_norm in ("all", "settings")
    want_filters = section_norm in ("all", "filters")

    conn = BWConnection.from_env()
    try:
        response = _bw_request(
            conn,
            f"/sap/bw/modeling/dtpa/{dtp_id}",
            accept="application/vnd.sap.bw.modeling.dtpa-v1_0_0+xml",
        )
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "dtp": dtp_id,
        }

    root = ET.fromstring(response.text)
    result: dict = {"dtp": root.get("name", dtp_id)}

    if want_info:
        result["description"] = root.get("description", "")
        source = next((c for c in root.iter() if _dtp_local(c.tag) == "source"), None)
        target = next((c for c in root.iter() if _dtp_local(c.tag) == "target"), None)
        result["source"] = _dtp_endpoint_summary(source)
        result["target"] = _dtp_endpoint_summary(target)

    if want_settings:
        settings: dict = {}
        ext = next(
            (c for c in root.iter() if _dtp_local(c.tag) == "extractionSettings"),
            None,
        )
        if ext is not None:
            mode = ext.get("extractionMode", "")
            settings["extractionMode"] = mode
            if mode in _DTP_EXTRACTION_MODES:
                settings["extractionModeLabel"] = _DTP_EXTRACTION_MODES[mode]
            for attr in ("allowedExtractionModes", "packageSize", "parallelExtraction"):
                if ext.get(attr) is not None:
                    settings[attr] = ext.get(attr)
        exe = next(
            (c for c in root.iter() if _dtp_local(c.tag) == "execution"), None
        )
        if exe is not None:
            pmode = exe.get("processingMode", "")
            settings["processingMode"] = pmode
            if pmode in _DTP_PROCESSING_MODES:
                settings["processingModeLabel"] = _DTP_PROCESSING_MODES[pmode]
        err = next(
            (c for c in root.iter() if _dtp_local(c.tag) == "errorHandling"), None
        )
        if err is not None:
            settings["errorHandling"] = {
                "requestHandling": err.get("requestHandling", ""),
                "numberOfErrorsPerPackage": err.get("numberOfErrorsPerPackage", ""),
            }
        result["settings"] = settings

    if want_filters:
        all_filters = _parse_dtp_filters(root)
        needle = name_filter.strip().lower()
        if needle:
            filters = [
                f
                for f in all_filters
                if needle in f.get("field", "").lower()
                or needle in f.get("description", "").lower()
            ]
            result["filterTotal"] = len(all_filters)
        else:
            filters = all_filters
        result["filterCount"] = len(filters)
        result["filters"] = filters

    return result


@mcp.tool
def get_data_flow(
    object_name: str, object_type: str = "IOBJ", section: str = "all"
) -> dict:
    """
    Show the data flow (lineage) around a BW object: what feeds INTO it and
    what it feeds OUT to, via transformations and their DataSources.

    Uses the BW Modeling where-used (xref) service to find related
    transformations, then reads each transformation's source and target to
    determine direction reliably (rather than parsing titles). For inbound
    transformations whose source is a DataSource, the DataSource name and
    source system are resolved.

    The full response can be large - the DTP sections in particular repeat
    status and process-chain details for every DTP, and resolving them costs
    one extra request per DTP. Use ``section`` to fetch only the part you need;
    when no DTP section is requested, the per-DTP resolution is skipped
    entirely (faster as well as smaller). To answer "which transformations
    target/read object X", use section "transformations" (or "inbound" /
    "outbound").

    Args:
        object_name: Technical name of the object (e.g. "0COSTCENTER").
        object_type: Object type code (default "IOBJ"). Also e.g. ADSO, HCPR.
        section: Which part(s) to return. One of:
            "all" (default) - transformations + DTPs, both directions;
            "transformations" - inbound + outbound transformations only;
            "inbound" - inbound transformations only;
            "outbound" - outbound transformations only;
            "dtps" - inbound + outbound DTPs only;
            "inbound_dtps" - inbound DTPs only;
            "outbound_dtps" - outbound DTPs only.
            Sections not requested are omitted (their count fields too).

    Returns:
        Dictionary with the requested section(s):
          - inbound: transformations that load INTO the object (target = it),
            each with transformationId, source (dataSource/sourceSystem/type),
            and target subType (ATTR/TEXT/HIER for InfoObjects).
          - outbound: transformations that read the object OUT to another
            target (source = it), each with transformationId and target.
          - inboundDataTransferProcesses: DTPs whose TARGET is this object,
            each with dtp id, source, status (objectStatus/contentState/
            version), and processChains (the process chains that run the DTP).
          - outboundDataTransferProcesses: DTPs whose SOURCE is this object,
            each with dtp id, target, status, and processChains.

    Process-chain usage is read from the DTP model itself (its
    usageInProcessChains section), so each DTP entry lists the process chains
    that execute it.
    """
    section_norm = (section or "all").strip().lower()
    valid_sections = {
        "all",
        "transformations",
        "inbound",
        "outbound",
        "dtps",
        "inbound_dtps",
        "outbound_dtps",
    }
    if section_norm not in valid_sections:
        return {
            "error": (
                f"Invalid section '{section}'. Valid: all, transformations, "
                "inbound, outbound, dtps, inbound_dtps, outbound_dtps."
            ),
            "object": object_name,
        }

    want_inbound_trfn = section_norm in ("all", "transformations", "inbound")
    want_outbound_trfn = section_norm in ("all", "transformations", "outbound")
    want_inbound_dtp = section_norm in ("all", "dtps", "inbound_dtps")
    want_outbound_dtp = section_norm in ("all", "dtps", "outbound_dtps")
    want_any_trfn = want_inbound_trfn or want_outbound_trfn
    want_any_dtp = want_inbound_dtp or want_outbound_dtp

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

    result: dict = {
        "object": object_name,
        "objectType": object_type.upper(),
    }

    # Resolve transformation endpoints only if a transformation section is
    # requested (each resolution is a separate request).
    if want_any_trfn:
        inbound = []
        outbound = []
        for tid in transformation_ids:
            endpoints = _get_transformation_endpoints(tid)
            src = endpoints.get("source", {})
            tgt = endpoints.get("target", {})
            if tgt.get("name", "").upper() == target_name_upper:
                if want_inbound_trfn:
                    inbound.append(
                        {
                            "transformationId": tid,
                            "source": src,
                            "targetSubType": tgt.get("subType", ""),
                        }
                    )
            elif src.get("name", "").upper().startswith(target_name_upper):
                if want_outbound_trfn:
                    outbound.append({"transformationId": tid, "target": tgt})

        if want_inbound_trfn:
            result["inboundCount"] = len(inbound)
            result["inbound"] = inbound
        if want_outbound_trfn:
            result["outboundCount"] = len(outbound)
            result["outbound"] = outbound

    # Split DTPs by direction using each DTP's actual source/target, and
    # attach the DTP's status. A DTP is inbound when its target is this object.
    # Skip the per-DTP resolution entirely when no DTP section is requested.
    if want_any_dtp:
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
            process_chains = endpoints.get("processChains", [])
            if tgt.get("name", "").upper() == target_name_upper:
                if want_inbound_dtp:
                    inbound_dtps.append(
                        {
                            "dtp": dtp,
                            "source": src,
                            "status": status,
                            "processChains": process_chains,
                        }
                    )
            elif src.get("name", "").upper().startswith(target_name_upper):
                if want_outbound_dtp:
                    outbound_dtps.append(
                        {
                            "dtp": dtp,
                            "target": tgt,
                            "status": status,
                            "processChains": process_chains,
                        }
                    )

        if want_inbound_dtp:
            result["inboundDataTransferProcessCount"] = len(inbound_dtps)
            result["inboundDataTransferProcesses"] = inbound_dtps
        if want_outbound_dtp:
            result["outboundDataTransferProcessCount"] = len(outbound_dtps)
            result["outboundDataTransferProcesses"] = outbound_dtps

    return result


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


# ---------------------------------------------------------------------------
# ADT repository - read-only access to ABAP source code
#
# Objects are resolved through the ADT repository information system search
# (/sap/bc/adt/repository/informationsystem/search, operation=quickSearch),
# which returns each object's ADT URI - e.g.
#   PROG/P  /sap/bc/adt/programs/programs/zsandbox
#   CLAS/OC /sap/bc/adt/oo/classes/%2fbic%2fjvwyp715zshw1j8tbvi4_a
#   FUGR/FF /sap/bc/adt/functions/groups/rsdri/fmodules/rsdri_infoprov_read
# and the source is served (as text/plain) at <uri>/source/main. Classes also
# have separate includes at <uri>/includes/<definitions|implementations|
# macros|testclasses>. These tools only issue GETs - nothing is locked,
# changed or activated.
# ---------------------------------------------------------------------------

# Friendly type aliases -> ADT object type codes used by the search.
_ADT_TYPE_ALIASES = {
    "PROG": "PROG/P",
    "PROGRAM": "PROG/P",
    "REPORT": "PROG/P",
    "INCL": "PROG/I",
    "INCLUDE": "PROG/I",
    "CLAS": "CLAS/OC",
    "CLASS": "CLAS/OC",
    "INTF": "INTF/OI",
    "INTERFACE": "INTF/OI",
    "FUNC": "FUGR/FF",
    "FM": "FUGR/FF",
    "FUNCTION": "FUGR/FF",
    "FUGR": "FUGR/F",
    "DDLS": "DDLS/DF",
    "CDS": "DDLS/DF",
    "TABL": "TABL/DT",
    "TABLE": "TABL/DT",
}

# Object types whose source is served at <uri>/source/main.
_ADT_SOURCE_TYPES = {"PROG/P", "PROG/I", "CLAS/OC", "INTF/OI", "FUGR/FF", "DDLS/DF", "TABL/DT"}

_ADT_CLASS_INCLUDES = ("definitions", "implementations", "macros", "testclasses")


def _adt_get(conn: BWConnection, path: str, accept: str = "*/*", params: dict | None = None) -> requests.Response:
    """Authenticated GET against an ADT resource (read-only, no CSRF needed)."""
    response = requests.get(
        f"{conn.base_url}{path}",
        auth=(conn.username, conn.password),
        headers={"Accept": accept, "sap-client": conn.client},
        params=params,
        verify=conn.verify_ssl,
        timeout=60,
    )
    response.raise_for_status()
    return response


def _adt_search(conn: BWConnection, query: str, adt_type: str = "", max_results: int = 50) -> list[dict]:
    params = {"operation": "quickSearch", "query": query, "maxResults": max_results}
    if adt_type:
        params["objectType"] = adt_type
    response = _adt_get(
        conn,
        "/sap/bc/adt/repository/informationsystem/search",
        accept="application/xml",
        params=params,
    )
    root = ET.fromstring(response.text)
    results = []
    for ref in root.iter():
        if _adt_local(ref.tag) != "objectReference":
            continue
        a = _adt_attrs(ref)
        results.append(
            {
                "name": a.get("name", ""),
                "type": a.get("type", ""),
                "description": a.get("description", ""),
                "package": a.get("packageName", ""),
                "uri": a.get("uri", ""),
            }
        )
    return results


def _adt_normalize_type(object_type: str) -> str:
    t = (object_type or "").strip().upper()
    return _ADT_TYPE_ALIASES.get(t, t)


def _abap_extract_method(source: str, method: str) -> str:
    """Return the METHOD <name>. ... ENDMETHOD. block from a class source."""
    pattern = re.compile(
        rf"^[ \t]*METHOD[ \t]+{re.escape(method)}\b.*?^[ \t]*ENDMETHOD[ \t]*\.",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(source)
    return m.group(0) if m else ""


@mcp.tool
def search_abap_objects(
    search_term: str, object_type: str = "", max_results: int = 50
) -> dict:
    """
    Search the ABAP repository (via ADT) for programs, classes, interfaces,
    function modules, CDS views, tables, packages, ... by name.

    Read-only. Use it to find the exact name/type before calling
    get_abap_source, or to answer "which Z programs/classes exist for X".

    Args:
        search_term: Name or pattern; "*" is a wildcard (e.g. "ZSAND*",
            "/BIC/JVWYP*", "CL_CTS_HDI*"). Case-insensitive.
        object_type: Optional filter - an ADT type code ("PROG/P", "CLAS/OC",
            "INTF/OI", "FUGR/FF", "DDLS/DF", "TABL/DT", "DEVC/K", ...) or a
            friendly alias (PROGRAM, INCLUDE, CLASS, INTERFACE, FUNCTION, CDS,
            TABLE). Leave empty for all types.
        max_results: Maximum number of hits (default 50).

    Returns:
        Dictionary with the search term, count and ``results`` - each with
        name, type (ADT type code), description, package and ADT uri.
    """
    conn = BWConnection.from_env()
    adt_type = _adt_normalize_type(object_type)
    try:
        results = _adt_search(conn, search_term, adt_type, max_results)
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "detail": e.response.text[:500],
            "searchTerm": search_term,
        }
    return {"searchTerm": search_term, "objectType": adt_type, "count": len(results), "results": results}


@mcp.tool
def get_abap_source(
    object_name: str,
    object_type: str = "",
    method: str = "",
    include_class_includes: bool = False,
    start_line: int = 1,
    max_lines: int = 0,
) -> dict:
    """
    Read the ABAP source code of a program, include, class, interface,
    function module, CDS view or table definition (via ADT). Read-only.

    Typical uses: read a Z report, a transformation's generated AMDP class
    (e.g. "/BIC/JVWYP715ZSHW1J8TBVI4_A" - see classNameA in
    get_transformation_details) or one of its methods (method="GLOBAL_END"),
    an SAP standard class/report, or a function module.

    Args:
        object_name: Technical name (e.g. "ZSANDBOX", "CL_CTS_HDI_CONTAINER_API",
            "RSDRI_INFOPROV_READ"). Namespaced names like "/BIC/..." work.
        object_type: Optional, to disambiguate names that exist as several
            types (e.g. a package and a program both named ZSANDBOX): ADT
            type code (PROG/P, PROG/I, CLAS/OC, INTF/OI, FUGR/FF, DDLS/DF,
            TABL/DT) or alias (PROGRAM, INCLUDE, CLASS, INTERFACE, FUNCTION,
            CDS, TABLE). If empty, the first object with readable source is
            used.
        method: For classes/interfaces - return only this method's
            METHOD ... ENDMETHOD block (plus its line position) instead of the
            whole source. Searched in the main source and, for classes, the
            local implementations include.
        include_class_includes: For classes - also return the local
            definitions, local implementations, macros and test classes
            includes (non-empty ones only).
        start_line / max_lines: Page through long sources (1-based start;
            max_lines=0 returns everything from start_line). Ignored when
            method is set.

    Returns:
        Dictionary with name, type, description, package, uri, totalLines,
        and either ``source`` (with startLine/returnedLines/hasMore) or
        ``method`` + ``methodSource``; for classes optionally ``includes``.
    """
    conn = BWConnection.from_env()
    adt_type = _adt_normalize_type(object_type)

    try:
        hits = _adt_search(conn, object_name, adt_type, max_results=20)
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "detail": e.response.text[:500],
            "object": object_name,
        }

    exact = [h for h in hits if h["name"].upper() == object_name.strip().upper()]
    candidates = [h for h in exact if h["type"] in _ADT_SOURCE_TYPES]
    if not candidates:
        return {
            "error": "No object with readable source found.",
            "object": object_name,
            "objectType": adt_type,
            "matches": exact or hits[:10],
            "hint": "Check the name with search_abap_objects; supported types: "
            + ", ".join(sorted(_ADT_SOURCE_TYPES)),
        }
    obj = candidates[0]

    try:
        source = _adt_get(conn, f"{obj['uri']}/source/main", accept="text/plain").text
    except requests.exceptions.HTTPError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.reason}",
            "detail": e.response.text[:500],
            "object": obj,
        }
    source = source.replace("\r\n", "\n")
    lines = source.split("\n")

    result: dict = {k: obj[k] for k in ("name", "type", "description", "package", "uri")}
    if len(candidates) > 1:
        result["otherTypes"] = [c["type"] for c in candidates[1:]]
    result["totalLines"] = len(lines)

    includes: dict = {}
    if obj["type"] == "CLAS/OC" and (include_class_includes or method):
        for inc in _ADT_CLASS_INCLUDES:
            try:
                text = _adt_get(conn, f"{obj['uri']}/includes/{inc}", accept="text/plain").text
            except requests.exceptions.HTTPError:
                continue
            text = text.replace("\r\n", "\n")
            # Skip the generated placeholder-only includes.
            meaningful = [
                l for l in text.split("\n")
                if l.strip() and not l.lstrip().startswith(('*"*', "*"))
            ]
            if meaningful:
                includes[inc] = text

    if method:
        block = _abap_extract_method(source, method)
        where = "main"
        if not block and "implementations" in includes:
            block = _abap_extract_method(includes["implementations"], method)
            where = "implementations"
        if not block:
            result["error"] = f"Method {method} not found."
            return result
        base = source if where == "main" else includes["implementations"]
        result["method"] = method.upper()
        result["methodFoundIn"] = where
        result["methodStartLine"] = base[: base.find(block)].count("\n") + 1
        result["methodSource"] = block
        return result

    start = max(start_line, 1)
    end = len(lines) if max_lines <= 0 else min(len(lines), start - 1 + max_lines)
    result["startLine"] = start
    result["returnedLines"] = max(end - start + 1, 0)
    result["hasMore"] = end < len(lines)
    result["source"] = "\n".join(lines[start - 1 : end])
    if include_class_includes and includes:
        result["includes"] = includes
    return result


def main() -> None:
    """Console-script entry point: start the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
