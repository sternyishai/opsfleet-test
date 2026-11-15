"""The analytics schema the agent may see: the allowlist and its column descriptions (HLD 03).

One record serves ``get_schema`` and the query guard, so what the model is shown and what the
guard lets through cannot disagree. PII columns (``first_name``, ``last_name``, ``email``,
``street_address``, ``postal_code``, ``latitude``, ``longitude``) and the location columns that
narrow a Customer down (``city``, ``user_geom``) are absent by design, not filtered later.
"""

from pydantic import BaseModel

from agent.domain.query import TableName


class ColumnSpec(BaseModel):
    name: str
    type: str
    description: str
    order_level: bool = False
    """Counts every item of a mixed-Brand order, so a Manager's scoped view drops it."""


class TableSpec(BaseModel):
    name: TableName
    description: str
    columns: list[ColumnSpec]


def _c(name: str, type_: str, description: str, *, order_level: bool = False) -> ColumnSpec:
    return ColumnSpec(name=name, type=type_, description=description, order_level=order_level)


ALLOWLIST: tuple[TableSpec, ...] = (
    TableSpec(
        name="orders",
        description="One row per order. An order can hold items of several Brands.",
        columns=[
            _c("order_id", "INTEGER", "Order ID."),
            _c("user_id", "INTEGER", "The Customer who placed the order (users.id)."),
            _c("status", "STRING", "Processing, Shipped, Complete, Cancelled or Returned."),
            _c("gender", "STRING", "The Customer's gender, M or F."),
            _c("created_at", "TIMESTAMP", "When the order was placed."),
            _c("returned_at", "TIMESTAMP", "When the order was returned; NULL if not."),
            _c("shipped_at", "TIMESTAMP", "When the order shipped; NULL if not yet."),
            _c("delivered_at", "TIMESTAMP", "When the order was delivered; NULL if not yet."),
            _c(
                "num_of_item",
                "INTEGER",
                "Items in the whole order, across all Brands.",
                order_level=True,
            ),
        ],
    ),
    TableSpec(
        name="order_items",
        description="One row per item sold. Revenue is SUM(sale_price) over this table.",
        columns=[
            _c("id", "INTEGER", "Order item ID."),
            _c("order_id", "INTEGER", "The order (orders.order_id)."),
            _c("user_id", "INTEGER", "The Customer (users.id)."),
            _c("product_id", "INTEGER", "The product (products.id)."),
            _c("status", "STRING", "Processing, Shipped, Complete, Cancelled or Returned."),
            _c("created_at", "TIMESTAMP", "When the item was ordered."),
            _c("shipped_at", "TIMESTAMP", "When the item shipped; NULL if not yet."),
            _c("delivered_at", "TIMESTAMP", "When the item was delivered; NULL if not yet."),
            _c("returned_at", "TIMESTAMP", "When the item was returned; NULL if not."),
            _c("sale_price", "FLOAT", "Price the item sold for, in USD."),
        ],
    ),
    TableSpec(
        name="products",
        description="The product catalog.",
        columns=[
            _c("id", "INTEGER", "Product ID."),
            _c("cost", "FLOAT", "Unit cost in USD; margin is sale_price - cost."),
            _c("category", "STRING", "Product category, e.g. Jeans, Outerwear & Coats."),
            _c("name", "STRING", "Product name."),
            _c("brand", "STRING", "Brand, exact case (e.g. 'adidas', 'PUMA', 'Calvin Klein')."),
            _c("retail_price", "FLOAT", "List price in USD."),
            _c("department", "STRING", "Men or Women."),
            _c("sku", "STRING", "Stock-keeping unit."),
            _c("distribution_center_id", "INTEGER", "Distribution center the product ships from."),
        ],
    ),
    TableSpec(
        name="users",
        description="Customers (shoppers), identified only by id. No names or contact details.",
        columns=[
            _c("id", "INTEGER", "Customer ID."),
            _c("age", "INTEGER", "Age in years."),
            _c("gender", "STRING", "M or F."),
            _c("state", "STRING", "State or region, e.g. 'California', 'Texas'."),
            _c("country", "STRING", "Country, e.g. 'United States', 'Brasil'."),
            _c("traffic_source", "STRING", "Search, Organic, Facebook, Email or Display."),
            _c("created_at", "TIMESTAMP", "When the Customer signed up."),
        ],
    ),
)
