"""
BW Modeling API - InfoArea Explorer

Connects to a SAP BW4/HANA system via the BW Modeling API
and lists all InfoAreas available in the system.
"""

import argparse
import getpass
import sys
import xml.etree.ElementTree as ET

import requests
import urllib3

# Suppress InsecureRequestWarning for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# XML namespaces used in BW Modeling API responses
NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "bwModel": "http://www.sap.com/bw/modeling",
}


def connect_and_list_infoareas(
    base_url: str,
    username: str,
    password: str,
    client: str = "100",
    max_results: int = 500,
) -> list[dict]:
    """
    Connect to the BW Modeling API and retrieve all InfoAreas.

    Args:
        base_url: Base URL of the BW system (e.g. https://bw4hana-dev.sap.example.com)
        username: SAP username
        password: SAP password
        client: SAP client number (default: 100)
        max_results: Maximum number of results to retrieve (default: 500)

    Returns:
        List of dicts with InfoArea name and description.
    """
    # Normalize base URL
    base_url = base_url.rstrip("/")

    # Build the bwsearch URL for InfoAreas (objectType=AREA)
    search_url = (
        f"{base_url}/sap/bw/modeling/repo/is/bwsearch"
        f"?searchTerm=*&maxSize={max_results}&objectType=AREA&searchInName=true"
    )

    headers = {
        "Accept": "application/xml",
        "sap-client": client,
    }

    print(f"\nConnecting to: {base_url}")
    print(f"User: {username} | Client: {client}")
    print(f"Fetching InfoAreas (max {max_results})...\n")

    try:
        response = requests.get(
            search_url,
            auth=(username, password),
            headers=headers,
            verify=False,  # BW systems often use self-signed certs
            timeout=60,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Could not connect to {base_url}")
        print(f"  Detail: {e}")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        if response.status_code == 401:
            print("ERROR: Authentication failed. Check username/password.")
        elif response.status_code == 403:
            print("ERROR: Access forbidden. Check user authorizations.")
        else:
            print(f"ERROR: HTTP {response.status_code} - {e}")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("ERROR: Request timed out.")
        sys.exit(1)

    # Parse XML response
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as e:
        print(f"ERROR: Could not parse response XML: {e}")
        sys.exit(1)

    # Extract InfoArea entries
    infoareas = []
    entries = root.findall("atom:entry", NAMESPACES)

    for entry in entries:
        obj_elem = entry.find(
            "atom:content/bwModel:object", NAMESPACES
        )
        title_elem = entry.find("atom:title", NAMESPACES)

        if obj_elem is not None:
            name = obj_elem.get("objectName", "")
            description = title_elem.text if title_elem is not None else ""
            infoareas.append({"name": name, "description": description})

    return infoareas


def display_infoareas(infoareas: list[dict]) -> None:
    """Display InfoAreas in a formatted table."""
    if not infoareas:
        print("No InfoAreas found.")
        return

    # Calculate column widths
    max_name = max(len(ia["name"]) for ia in infoareas)
    max_name = max(max_name, len("InfoArea"))

    # Print header
    print(f"{'InfoArea':<{max_name}}  Description")
    print(f"{'-' * max_name}  {'-' * 50}")

    # Print rows
    for ia in sorted(infoareas, key=lambda x: x["name"]):
        print(f"{ia['name']:<{max_name}}  {ia['description']}")

    print(f"\nTotal: {len(infoareas)} InfoArea(s)")


def main():
    parser = argparse.ArgumentParser(
        description="List all InfoAreas from a SAP BW4/HANA system via the BW Modeling API."
    )
    parser.add_argument(
        "--url",
        help="Base URL of the BW system (e.g. https://bw4hana-dev.sap.example.com)",
    )
    parser.add_argument("--user", help="SAP username")
    parser.add_argument("--password", help="SAP password (will prompt if not provided)")
    parser.add_argument("--client", default="100", help="SAP client (default: 100)")
    parser.add_argument(
        "--max-results",
        type=int,
        default=500,
        help="Maximum number of results (default: 500)",
    )

    args = parser.parse_args()

    # Interactive prompts for missing values
    base_url = args.url
    if not base_url:
        base_url = input("BW System URL (e.g. https://bw4hana-dev.sap.example.com): ").strip()
        if not base_url:
            print("ERROR: URL is required.")
            sys.exit(1)

    username = args.user
    if not username:
        username = input("Username: ").strip()
        if not username:
            print("ERROR: Username is required.")
            sys.exit(1)

    password = args.password
    if not password:
        password = getpass.getpass("Password: ")
        if not password:
            print("ERROR: Password is required.")
            sys.exit(1)

    client = args.client

    # Fetch and display
    infoareas = connect_and_list_infoareas(
        base_url=base_url,
        username=username,
        password=password,
        client=client,
        max_results=args.max_results,
    )
    display_infoareas(infoareas)


if __name__ == "__main__":
    main()
