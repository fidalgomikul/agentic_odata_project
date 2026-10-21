from mcp.server.mcpserver import MCPServer
import httpx

mcp = MCPServer("northwind")
BASE = "https://services.odata.org/V4/Northwind/Northwind.svc"


def _escape(value: str) -> str:
    """Escape single quotes for use inside an OData string literal."""
    return value.replace("'", "''")


def _get(entity: str, params: dict) -> list[dict]:
    """Run a GET against an OData entity set and return the rows."""
    r = httpx.get(f"{BASE}/{entity}", params=params, timeout=10)
    r.raise_for_status()
    rows = r.json()["value"]
    return [
        {key: value for key, value in row.items() if not key.startswith("@")}
        for row in rows
    ]

@mcp.tool()
def list_customers(name: str | None = None, country: str | None = None, top: int = 10) -> list[dict]:
    """Search customers. 'name' matches any part of the company name,
    case-insensitive (e.g. 'futter' finds 'Alfreds Futterkiste').
    'country' is an exact country name. Both are optional and can be combined.
    Returns at most 'top' results."""
    params = {"$top": top, "$select": "CustomerID,CompanyName,Country"}
    filters = []
    if name:
        filters.append(f"contains(tolower(CompanyName), '{_escape(name.lower())}')")
    if country:
        filters.append(f"Country eq '{_escape(country)}'")
    params = {"$top": top, "$select": "CustomerID,CompanyName,Country"}
    if filters:
        params["$filter"] = " and ".join(filters)
    return _get("Customers", params)


@mcp.tool()
def get_orders_for_customer(customer_name: str | None = None, customer_id: str | None = None, top: int = 10) -> list[dict]:
    """Get recent orders for one customer, newest first.
    Provide customer_id (e.g. 'ALFKI') if you know it, or customer_name
    (any part of the company name). If the name matches several customers,
    an error asks you to be more specific or use list_customers to pick an ID."""

    if customer_name and not customer_id:
        # Find the customer ID based on the name
        customer = next((customer for customer in list_customers(name=customer_name, top=1)), None)
        if not customer:
            raise ValueError("Customer not found")
        customer_id = customer["CustomerID"]

    params = {
        "$filter": f"CustomerID eq '{_escape(customer_id)}'",
        "$select": "OrderID,OrderDate,ShipCountry",
        "$orderby": "OrderDate desc",
        "$top": top,
    }
    return _get("Orders", params)


@mcp.tool()
def list_categories() -> list[dict]:
    """List all product categories. Use this to find valid category names
    before calling get_products_by_category."""
    return _get("Categories", {"$select": "CategoryID,CategoryName,Description"})


@mcp.tool()
def get_products_by_category(category_name: str, top: int = 20) -> list[dict]:
    """List products in a category, by category name (e.g. 'Beverages').
    Returns product ID, name and unit price."""
    params = {
        "$filter": f"Category/CategoryName eq '{_escape(category_name)}'",
        "$select": "ProductID,ProductName,UnitPrice",
        "$top": top,
    }
    return _get("Products", params)


if __name__ == "__main__":
    mcp.run()