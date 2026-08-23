"""
MCP Server for SAP BW Modeling API

Exposes SAP BW4/HANA modeling resources (InfoAreas, InfoObjects, ADSOs,
CompositeProviders, Queries, etc.) as MCP tools via FastMCP.
"""

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
        "This MCP server provides access to SAP BW4/HANA modeling objects "
        "via the BW Modeling API. You can search and explore InfoAreas, "
        "InfoObjects, ADSOs, CompositeProviders, Queries, and more."
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

    Args:
        search_term: Filter by name pattern (supports * wildcard).
        info_area: Filter by InfoArea technical name. Leave empty for all.
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
        "HCPR": "application/vnd.sap.bw.modeling.hcpr-v1_0_0+xml",
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

    Args:
        object_name: Technical name of the CompositeProvider (e.g. "ZSDHCPR01").

    Returns:
        List of part providers with their names and join/union type.
    """
    conn = BWConnection.from_env()

    path = f"/sap/bw/modeling/hcpr/{object_name}"

    try:
        response = _bw_request(
            conn, path, accept="application/vnd.sap.bw.modeling.hcpr-v1_0_0+xml"
        )
        root = ET.fromstring(response.text)
        parts = []
        entries = root.findall("atom:entry", NAMESPACES)

        for entry in entries:
            title = entry.find("atom:title", NAMESPACES)
            content = entry.find("atom:content", NAMESPACES)

            part = {
                "name": title.text if title is not None else "",
            }

            if content is not None:
                props = content.find("m:properties", NAMESPACES)
                if props is not None:
                    for child in props:
                        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child.text:
                            part[tag] = child.text

            parts.append(part)

        return parts
    except requests.exceptions.HTTPError as e:
        return [{"error": f"HTTP {e.response.status_code}: {e.response.reason}", "object": object_name}]


@mcp.tool
def get_infoobject_details(infoobject_name: str) -> dict:
    """
    Get detailed properties of an InfoObject (characteristic or key figure).

    Args:
        infoobject_name: Technical name (e.g. "0MATERIAL", "0PLANT", "ZCUSTOMER").

    Returns:
        Dictionary with InfoObject properties (type, data type, length, etc.).
    """
    return get_object_details(infoobject_name, "IOBJ")


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
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Console-script entry point: start the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
