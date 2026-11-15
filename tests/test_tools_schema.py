"""get_schema shows only allowlisted, non-PII columns, as the User's scoped view projects them."""

import pytest

from agent.contracts.tools import GetSchemaInput
from agent.domain.identity import Role, UserContext
from agent.tools.analysis import get_schema
from tests.fakes import turn_context

# HLD 03 § PII: never in, never out.
PII_COLUMNS = {
    "first_name",
    "last_name",
    "email",
    "street_address",
    "postal_code",
    "latitude",
    "longitude",
}

CEO = UserContext(user_id="ceo", role=Role.EXECUTIVE, access_version=1)
MANAGER = UserContext(
    user_id="m", role=Role.MANAGER, brands=frozenset({"Carhartt", "Columbia"}), access_version=1
)


@pytest.mark.parametrize("user", [CEO, MANAGER], ids=["executive", "manager"])
def test_no_pii_column_in_any_output(user: UserContext) -> None:
    out = get_schema(GetSchemaInput(), turn_context(user))

    assert {t.name for t in out.tables} == {"orders", "order_items", "products", "users"}
    for table in out.tables:
        assert not PII_COLUMNS & {c.name for c in table.columns}, table.name


def test_tables_filter_returns_only_those_tables() -> None:
    out = get_schema(GetSchemaInput(tables=["products"]), turn_context(CEO))

    assert [t.name for t in out.tables] == ["products"]


def test_manager_view_lacks_num_of_item() -> None:
    manager = get_schema(GetSchemaInput(tables=["orders"]), turn_context(MANAGER))
    executive = get_schema(GetSchemaInput(tables=["orders"]), turn_context(CEO))

    assert "num_of_item" not in {c.name for c in manager.tables[0].columns}
    assert "num_of_item" in {c.name for c in executive.tables[0].columns}


def test_scope_note_names_the_brands() -> None:
    assert get_schema(GetSchemaInput(), turn_context(MANAGER)).scope_note == (
        "You can analyze Carhartt and Columbia."
    )
    assert get_schema(GetSchemaInput(), turn_context(CEO)).scope_note == (
        "You can analyze all Brands."
    )
