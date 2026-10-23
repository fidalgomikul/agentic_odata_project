"""MCP server exposing the public Northwind OData service to AI agents.

The tools at the bottom are thin wrappers. All query-building and
result-cleaning logic lives in plain functions so it can be unit-tested
without any MCP machinery or network access.
"""

import httpx
from mcp.server.mcpserver import MCPServer

BASE = "https://services.odata.org/V4/Northwind/Northwind.svc"
MAX_TOP = 50

mcp = MCPServer("northwind")


# --------------------------------------------------------------------------
# Helpers (plain functions: easy to test)
# --------------------------------------------------------------------------


def escape_odata(value: str) -> str:
    """Escape single quotes for use inside an OData string literal."""
    return value.replace("'", "''")


def clamp_top(top: int) -> int:
    """Keep the requested row count between 1 and MAX_TOP."""
    return max(1, min(top, MAX_TOP))


def strip_metadata(rows: list[dict]) -> list[dict]:
    """Remove OData annotations (keys starting with '@', e.g. '@odata.etag')."""
    return [{k: v for k, v in row.items() if not k.startswith("@")} for row in rows]


def odata_get(entity: str, params: dict) -> list[dict]:
    """GET an OData entity set and return its rows without metadata keys."""
    response = httpx.get(f"{BASE}/{entity}", params=params, timeout=10)
    response.raise_for_status()
    return strip_metadata(response.json()["value"])


def build_customer_filter(name: str | None, country: str | None) -> str | None:
    """Build the $filter for customers; None when no filter is needed."""
    conditions = []
    if name:
        conditions.append(
            f"contains(tolower(CompanyName), '{escape_odata(name.lower())}')"
        )
    if country:
        conditions.append(f"Country eq '{escape_odata(country)}'")
    return " and ".join(conditions) or None


def find_customers(
    name: str | None = None,
    country: str | None = None,
    top: int = 10
) -> list[dict]:
    params: dict = {
        "$top": clamp_top(top),
        "$select": "CustomerID,CompanyName,Country",
    }
    odata_filter = build_customer_filter(name, country)
    if odata_filter:
        params["$filter"] = odata_filter
    return odata_get("Customers", params)


def resolve_customer_id(customer_id: str | None, customer_name: str | None) -> str:
    """Return a CustomerID, looking it up by name when needed."""
    if customer_id:
        return customer_id
    if not customer_name:
        raise ValueError("Provide either customer_id or customer_name.")

    matches = find_customers(name=customer_name, top=2)
    if not matches:
        raise ValueError(f"No customer found matching '{customer_name}'.")
    if len(matches) > 1:
        raise ValueError(
            f"Several customers match '{customer_name}'. Be more specific, "
            "or use list_customers to pick a CustomerID."
        )
    return matches[0]["CustomerID"]


def fetch_orders(
    customer_id: str | None = None,
    customer_name: str | None = None,
    top: int = 10,
) -> list[dict]:
    resolved_id = resolve_customer_id(customer_id, customer_name)
    params = {
        "$filter": f"CustomerID eq '{escape_odata(resolved_id)}'",
        "$select": "OrderID,OrderDate,ShipCountry",
        "$orderby": "OrderDate desc",
        "$top": clamp_top(top),
    }
    return odata_get("Orders", params)


def fetch_categories() -> list[dict]:
    return odata_get("Categories", {"$select": "CategoryID,CategoryName,Description"})


def fetch_products_by_category(category_name: str, top: int = 20) -> list[dict]:
    params = {
        "$filter": f"Category/CategoryName eq '{escape_odata(category_name)}'",
        "$select": "ProductID,ProductName,UnitPrice",
        "$top": clamp_top(top),
    }
    return odata_get("Products", params)


# --------------------------------------------------------------------------
# MCP tools (the interface the agent sees: names, docstrings, parameters)
# --------------------------------------------------------------------------


@mcp.tool()
def list_customers(
    name: str | None = None,
    country: str | None = None,
    top: int = 10,
) -> list[dict]:
    """Search customers. 'name' matches any part of the company name,
    case-insensitive (e.g. 'futter' finds 'Alfreds Futterkiste').
    'country' is an exact country name. Both are optional and can be combined.
    Returns at most 'top' results."""
    return find_customers(name, country, top)


@mcp.tool()
def get_orders_for_customer(
    customer_id: str | None = None,
    customer_name: str | None = None,
    top: int = 10,
) -> list[dict]:
    """Get recent orders for one customer, newest first.
    Provide customer_id (e.g. 'ALFKI') if you know it, or customer_name
    (any part of the company name). If the name matches several customers,
    an error asks you to be more specific or use list_customers to pick an ID."""
    return fetch_orders(customer_id, customer_name, top)


@mcp.tool()
def list_categories() -> list[dict]:
    """List all product categories. Use this to find valid category names
    before calling get_products_by_category."""
    return fetch_categories()


@mcp.tool()
def get_products_by_category(category_name: str, top: int = 20) -> list[dict]:
    """List products in a category, by category name (e.g. 'Beverages').
    Returns product ID, name and unit price."""
    return fetch_products_by_category(category_name, top)


if __name__ == "__main__":
    mcp.run()