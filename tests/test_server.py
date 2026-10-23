"""Unit tests for the Northwind MCP server.

No test touches the network: httpx.get is replaced by a fake that records
the request and returns canned rows.
"""

import asyncio

import pytest

import mpcserver
from mpcserver import (
    MAX_TOP,
    build_customer_filter,
    clamp_top,
    escape_odata,
    fetch_categories,
    fetch_orders,
    fetch_products_by_category,
    find_customers,
    resolve_customer_id,
    strip_metadata,
)


class FakeResponse:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return {"value": self._rows}


@pytest.fixture
def fake_get(monkeypatch):
    """Replace httpx.get. Set fake_get.rows to control the response,
    and read fake_get.calls to inspect what was requested."""

    class Recorder:
        def __init__(self):
            self.rows = []
            self.calls = []

        def __call__(self, url, params=None, timeout=None):
            self.calls.append({"url": url, "params": params, "timeout": timeout})
            return FakeResponse(self.rows)

    recorder = Recorder()
    monkeypatch.setattr(mpcserver.httpx, "get", recorder)
    return recorder


# --------------------------------------------------------------------------
# Small pure helpers
# --------------------------------------------------------------------------


def test_escape_doubles_single_quotes():
    assert escape_odata("O'Brien") == "O''Brien"


def test_escape_leaves_plain_text_alone():
    assert escape_odata("Germany") == "Germany"


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(10, 10), (0, 1), (-5, 1), (MAX_TOP, MAX_TOP), (MAX_TOP + 1, MAX_TOP), (10_000, MAX_TOP)],
)
def test_clamp_top(requested, expected):
    assert clamp_top(requested) == expected


def test_strip_metadata_removes_only_at_keys():
    rows = [{"@odata.etag": "W/1", "OrderID": 1, "ShipCountry": "Germany"}]
    assert strip_metadata(rows) == [{"OrderID": 1, "ShipCountry": "Germany"}]


def test_strip_metadata_handles_empty_list():
    assert strip_metadata([]) == []


# --------------------------------------------------------------------------
# Customer filter building
# --------------------------------------------------------------------------


def test_filter_none_when_no_arguments():
    assert build_customer_filter(None, None) is None


def test_filter_ignores_empty_strings():
    assert build_customer_filter("", "") is None


def test_filter_name_only_is_case_insensitive():
    assert (
        build_customer_filter("FuTTer", None)
        == "contains(tolower(CompanyName), 'futter')"
    )


def test_filter_country_only():
    assert build_customer_filter(None, "Germany") == "Country eq 'Germany'"


def test_filter_name_and_country_joined_with_and():
    assert build_customer_filter("futter", "Germany") == (
        "contains(tolower(CompanyName), 'futter') and Country eq 'Germany'"
    )


def test_filter_escapes_quotes_in_both_fields():
    result = build_customer_filter("o'brien", "Cote d'Ivoire")
    assert "'o''brien'" in result
    assert "'Cote d''Ivoire'" in result


# --------------------------------------------------------------------------
# find_customers
# --------------------------------------------------------------------------


def test_find_customers_without_filters_sends_no_filter(fake_get):
    find_customers()
    params = fake_get.calls[0]["params"]
    assert "$filter" not in params
    assert params["$top"] == 10
    assert params["$select"] == "CustomerID,CompanyName,Country"
    assert fake_get.calls[0]["url"].endswith("/Customers")


def test_find_customers_sends_filter_and_clamped_top(fake_get):
    find_customers(name="futter", country="Germany", top=9999)
    params = fake_get.calls[0]["params"]
    assert params["$filter"].startswith("contains(tolower(CompanyName)")
    assert params["$top"] == MAX_TOP


def test_find_customers_uses_a_timeout(fake_get):
    find_customers()
    assert fake_get.calls[0]["timeout"] is not None


def test_find_customers_strips_metadata_from_results(fake_get):
    fake_get.rows = [
        {"@odata.etag": "W/1", "CustomerID": "ALFKI", "CompanyName": "Alfreds Futterkiste"}
    ]
    assert find_customers(name="futter") == [
        {"CustomerID": "ALFKI", "CompanyName": "Alfreds Futterkiste"}
    ]


# --------------------------------------------------------------------------
# resolve_customer_id
# --------------------------------------------------------------------------


def test_resolve_prefers_explicit_id_and_makes_no_request(fake_get):
    assert resolve_customer_id("ALFKI", "ignored") == "ALFKI"
    assert fake_get.calls == []


def test_resolve_requires_id_or_name(fake_get):
    with pytest.raises(ValueError, match="customer_id or customer_name"):
        resolve_customer_id(None, None)


def test_resolve_looks_up_single_match(fake_get):
    fake_get.rows = [{"CustomerID": "ALFKI"}]
    assert resolve_customer_id(None, "futter") == "ALFKI"


def test_resolve_asks_for_two_rows_to_detect_ambiguity(fake_get):
    fake_get.rows = [{"CustomerID": "ALFKI"}]
    resolve_customer_id(None, "futter")
    assert fake_get.calls[0]["params"]["$top"] == 2


def test_resolve_raises_when_nothing_matches(fake_get):
    fake_get.rows = []
    with pytest.raises(ValueError, match="No customer found matching 'zzz'"):
        resolve_customer_id(None, "zzz")


def test_resolve_raises_when_name_is_ambiguous(fake_get):
    fake_get.rows = [{"CustomerID": "AAA"}, {"CustomerID": "BBB"}]
    with pytest.raises(ValueError, match="Several customers match"):
        resolve_customer_id(None, "a")


# --------------------------------------------------------------------------
# fetch_orders
# --------------------------------------------------------------------------


def test_orders_by_id_sends_one_request_newest_first(fake_get):
    fake_get.rows = [{"@odata.etag": "x", "OrderID": 1}]
    result = fetch_orders(customer_id="ALFKI", top=3)

    assert len(fake_get.calls) == 1
    params = fake_get.calls[0]["params"]
    assert fake_get.calls[0]["url"].endswith("/Orders")
    assert params["$filter"] == "CustomerID eq 'ALFKI'"
    assert params["$orderby"] == "OrderDate desc"
    assert params["$top"] == 3
    assert result == [{"OrderID": 1}]


def test_orders_by_name_resolves_id_then_fetches_orders(fake_get, monkeypatch):
    responses = iter([[{"CustomerID": "ALFKI"}], [{"OrderID": 7}]])

    def sequenced(url, params=None, timeout=None):
        fake_get.calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse(next(responses))

    monkeypatch.setattr(mpcserver.httpx, "get", sequenced)
    result = fetch_orders(customer_name="futter")

    assert [c["url"].rsplit("/", 1)[-1] for c in fake_get.calls] == ["Customers", "Orders"]
    assert fake_get.calls[1]["params"]["$filter"] == "CustomerID eq 'ALFKI'"
    assert result == [{"OrderID": 7}]


def test_orders_escape_quotes_in_customer_id(fake_get):
    fetch_orders(customer_id="A'B")
    assert fake_get.calls[0]["params"]["$filter"] == "CustomerID eq 'A''B'"


# --------------------------------------------------------------------------
# Categories and products
# --------------------------------------------------------------------------


def test_categories_request(fake_get):
    fake_get.rows = [{"CategoryID": 1, "CategoryName": "Beverages"}]
    assert fetch_categories() == [{"CategoryID": 1, "CategoryName": "Beverages"}]
    assert fake_get.calls[0]["url"].endswith("/Categories")


def test_products_filter_follows_category_navigation_property(fake_get):
    fetch_products_by_category("Beverages")
    params = fake_get.calls[0]["params"]
    assert fake_get.calls[0]["url"].endswith("/Products")
    assert params["$filter"] == "Category/CategoryName eq 'Beverages'"
    assert params["$top"] == 20


def test_products_escape_quotes_and_clamp_top(fake_get):
    fetch_products_by_category("Cat'egory", top=500)
    params = fake_get.calls[0]["params"]
    assert "'Cat''egory'" in params["$filter"]
    assert params["$top"] == MAX_TOP


def test_empty_result_is_an_empty_list_not_an_error(fake_get):
    fake_get.rows = []
    assert fetch_products_by_category("Drinks") == []


# --------------------------------------------------------------------------
# The MCP interface the agent actually sees
# --------------------------------------------------------------------------


def test_server_exposes_expected_tools_with_descriptions():
    tools = asyncio.run(mpcserver.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "list_customers",
        "get_orders_for_customer",
        "list_categories",
        "get_products_by_category",
    }
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"


def test_list_customers_description_mentions_name_search():
    tools = asyncio.run(mpcserver.mcp.list_tools())
    description = next(t for t in tools if t.name == "list_customers").description
    assert "case-insensitive" in description