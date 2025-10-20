"""Dynamic analytics engine powering the analytics workspace."""

from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import pytz
from dateutil.parser import parse as dateutil_parse

from .data_harmony import DataHarmonySnapshot, _table_exists


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _serialise_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serialise_value(entry) for entry in value]
    return value


def _ensure_iterable(value: Any) -> List[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        parsed = dateutil_parse(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"Could not parse date value '{value}'")
    return parsed.date()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        parsed = dateutil_parse(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_currency(value: float) -> str:
    return f"${value:,.2f}"


def _format_number(value: float) -> str:
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, remaining = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remaining}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _summary_entry(
    identifier: str,
    label: str,
    value: float,
    *,
    format_hint: str = "number",
    description: Optional[str] = None,
) -> Dict[str, Any]:
    display = (
        _format_currency(value)
        if format_hint == "currency"
        else _format_duration(value)
        if format_hint == "duration"
        else _format_number(value)
    )
    return {
        "id": identifier,
        "label": label,
        "value": round(float(value), 4),
        "display": display,
        "format": format_hint,
        "description": description,
    }


def _safe_timezone(tz_name: str) -> Optional[timezone]:
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        return None
    now = datetime.now(tz)
    return now.tzinfo


# ---------------------------------------------------------------------------
# Parameter and definition primitives
# ---------------------------------------------------------------------------


@dataclass
class ReportParameter:
    name: str
    label: str
    param_type: str
    description: str = ""
    required: bool = False
    default: Any = None
    multiple: bool = False
    options: Optional[List[Dict[str, Any]]] = None
    options_builder: Optional[Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = None
    placeholder: Optional[str] = None

    def describe(self, context: Dict[str, Any]) -> Dict[str, Any]:
        options = self.options
        if self.options_builder is not None:
            try:
                options = self.options_builder(context) or []
            except Exception:
                options = self.options or []
        default_value = self.default() if callable(self.default) else self.default
        return {
            "name": self.name,
            "label": self.label,
            "type": self.param_type,
            "description": self.description,
            "required": self.required,
            "multiple": self.multiple,
            "options": options,
            "default": _serialise_value(default_value),
            "placeholder": self.placeholder,
        }

    def normalise(self, value: Any) -> Any:
        candidate = value
        if candidate in (None, ""):
            candidate = self.default() if callable(self.default) else self.default
        if candidate in (None, ""):
            if self.required:
                raise ValueError(f"{self.label} is required")
            return [] if self.multiple else None
        if self.param_type == "date":
            return _parse_date(candidate)
        if self.param_type == "integer":
            return int(candidate)
        if self.param_type == "number":
            return float(candidate)
        if self.param_type == "boolean":
            if isinstance(candidate, bool):
                return candidate
            if isinstance(candidate, (int, float)):
                return bool(candidate)
            return str(candidate).strip().lower() in {"true", "1", "yes", "y"}
        if self.param_type == "enum":
            value_text = str(candidate)
            allowed = {option["value"] for option in self.options or []}
            if allowed and value_text not in allowed:
                raise ValueError(
                    f"Invalid value '{candidate}' for {self.label}; expected one of {sorted(allowed)}"
                )
            return value_text
        if self.param_type in {"multi", "multi_enum"} or self.multiple:
            values = _ensure_iterable(candidate)
            allowed = {option["value"] for option in self.options or []}
            if allowed:
                invalid = [value for value in values if value not in allowed]
                if invalid:
                    raise ValueError(
                        f"Invalid selections {invalid} for {self.label}; expected subset of {sorted(allowed)}"
                    )
            return values
        return candidate


@dataclass
class ReportDefinition:
    id: str
    name: str
    description: str
    parameters: List[ReportParameter]
    runner: Callable[[DataHarmonySnapshot, Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
    tags: List[str] = field(default_factory=list)

    def describe(self, context: Dict[str, Any]) -> Dict[str, Any]:
        defaults = {
            parameter.name: _serialise_value(
                parameter.default() if callable(parameter.default) else parameter.default
            )
            for parameter in self.parameters
        }
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "parameters": [parameter.describe(context) for parameter in self.parameters],
            "tags": self.tags,
            "defaultParams": defaults,
        }

    def normalise_params(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalised: Dict[str, Any] = {}
        for parameter in self.parameters:
            normalised[parameter.name] = parameter.normalise(payload.get(parameter.name))
        return normalised

    def serialise_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {name: _serialise_value(value) for name, value in params.items()}

    def run(
        self,
        snapshot: DataHarmonySnapshot,
        params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.runner(snapshot, params, context)


# ---------------------------------------------------------------------------
# Analytics engine
# ---------------------------------------------------------------------------


class AnalyticsEngine:
    def __init__(self) -> None:
        self._definitions: Dict[str, ReportDefinition] = {}
        self._register_default_reports()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def list_report_definitions(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        context = self._build_context(conn)
        return [definition.describe(context) for definition in self._definitions.values()]

    def run_report(
        self,
        conn: sqlite3.Connection,
        report_id: str,
        params: Optional[Dict[str, Any]],
        *,
        timezone_name: str = "UTC",
    ) -> Dict[str, Any]:
        if report_id not in self._definitions:
            raise KeyError(f"Unknown analytics report '{report_id}'")
        definition = self._definitions[report_id]
        context = self._build_context(conn)
        params_payload = params or {}
        normalised_params = definition.normalise_params(params_payload)
        snapshot = DataHarmonySnapshot.build(conn, timezone=timezone_name)
        result = definition.run(snapshot, normalised_params, context)
        tzinfo = _safe_timezone(timezone_name)
        generated_at = datetime.now(tzinfo or timezone.utc)
        meta_payload = result.get("meta", {})
        meta_payload.setdefault("appliedParameters", definition.serialise_params(normalised_params))
        response = {
            "id": definition.id,
            "name": definition.name,
            "description": definition.description,
            "generatedAt": generated_at.isoformat(),
            "summary": result.get("summary", []),
            "charts": result.get("charts", []),
            "tables": result.get("tables", []),
            "notes": result.get("notes", []),
            "meta": meta_payload,
            "dataSources": result.get("dataSources", []),
        }
        return response

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------
    def register(self, definition: ReportDefinition) -> None:
        self._definitions[definition.id] = definition

    def _register_default_reports(self) -> None:
        self.register(
            ReportDefinition(
                id="orders_overview",
                name="Orders Performance Overview",
                description="Revenue, volume, and pacing insights across all orders.",
                parameters=[
                    ReportParameter(
                        name="start_date",
                        label="Start Date",
                        param_type="date",
                        description="Limit calculations to orders on or after this date.",
                    ),
                    ReportParameter(
                        name="end_date",
                        label="End Date",
                        param_type="date",
                        description="Limit calculations to orders on or before this date.",
                    ),
                    ReportParameter(
                        name="statuses",
                        label="Statuses",
                        param_type="multi_enum",
                        multiple=True,
                        description="Only include orders matching these statuses.",
                        options_builder=lambda ctx: [
                            {"value": status, "label": status}
                            for status in ctx.get("order_statuses", [])
                        ],
                    ),
                    ReportParameter(
                        name="include_deleted",
                        label="Include Deleted Orders",
                        param_type="boolean",
                        description="Include orders explicitly marked as deleted.",
                        default=False,
                    ),
                ],
                runner=_run_orders_overview,
                tags=["orders", "revenue", "overview"],
            )
        )

        self.register(
            ReportDefinition(
                id="line_item_performance",
                name="Line Item Performance",
                description="Understand which catalog items or packages drive volume.",
                parameters=[
                    ReportParameter(
                        name="start_date",
                        label="Start Date",
                        param_type="date",
                    ),
                    ReportParameter(
                        name="end_date",
                        label="End Date",
                        param_type="date",
                    ),
                    ReportParameter(
                        name="grouping",
                        label="Group By",
                        param_type="enum",
                        default="catalog_item",
                        options=[
                            {"value": "catalog_item", "label": "Catalog Item"},
                            {"value": "package", "label": "Package"},
                            {"value": "order", "label": "Order"},
                        ],
                        description="Choose how line items are grouped for analysis.",
                    ),
                    ReportParameter(
                        name="top_n",
                        label="Top Results",
                        param_type="integer",
                        default=15,
                        description="Number of rows to display in the detailed table.",
                    ),
                ],
                runner=_run_line_item_performance,
                tags=["orders", "line_items", "catalog"],
            )
        )

        self.register(
            ReportDefinition(
                id="customer_performance",
                name="Customer Performance",
                description="Surface high-value customers and engagement trends.",
                parameters=[
                    ReportParameter(
                        name="start_date",
                        label="Start Date",
                        param_type="date",
                    ),
                    ReportParameter(
                        name="end_date",
                        label="End Date",
                        param_type="date",
                    ),
                    ReportParameter(
                        name="minimum_orders",
                        label="Minimum Orders",
                        param_type="integer",
                        default=1,
                        description="Only include customers with at least this many orders.",
                    ),
                    ReportParameter(
                        name="top_n",
                        label="Top Results",
                        param_type="integer",
                        default=10,
                    ),
                ],
                runner=_run_customer_performance,
                tags=["customers", "orders"],
            )
        )

        self.register(
            ReportDefinition(
                id="reminder_health",
                name="Reminder Health",
                description="Track completion velocity and upcoming commitments.",
                parameters=[
                    ReportParameter(
                        name="days_ahead",
                        label="Due Horizon (days)",
                        param_type="integer",
                        default=14,
                        description="Number of days ahead to consider "
                        "when flagging upcoming reminders.",
                    )
                ],
                runner=_run_reminder_health,
                tags=["reminders", "operations"],
            )
        )

        self.register(
            ReportDefinition(
                id="records_activity",
                name="Knowledge Base Activity",
                description="Usage overview for Data Harmony records, mentions, and actions.",
                parameters=[],
                runner=_run_records_activity,
                tags=["records", "mentions"],
            )
        )

        self.register(
            ReportDefinition(
                id="dataset_overview",
                name="Dataset Snapshot",
                description="Explore any tracked dataset with lightweight profiling.",
                parameters=[
                    ReportParameter(
                        name="dataset",
                        label="Dataset",
                        param_type="enum",
                        default="orders",
                        options_builder=_build_dataset_options,
                        description="Choose which dataset to profile.",
                    ),
                    ReportParameter(
                        name="sample_size",
                        label="Sample Size",
                        param_type="integer",
                        default=10,
                        description="Number of sample rows to include in the table output.",
                    ),
                ],
                runner=_run_dataset_overview,
                tags=["explore", "dataset"],
            )
        )

    # ------------------------------------------------------------------
    # Context gathering
    # ------------------------------------------------------------------
    def _build_context(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        order_statuses = []
        if _table_exists(conn, "orders"):
            cursor = conn.execute("SELECT DISTINCT status FROM orders WHERE status IS NOT NULL")
            order_statuses = sorted(
                {
                    (row["status"] if isinstance(row, sqlite3.Row) else row[0])
                    for row in cursor.fetchall()
                    if (row["status"] if isinstance(row, sqlite3.Row) else row[0])
                }
            )
        record_types = []
        if _table_exists(conn, "records"):
            cursor = conn.execute("SELECT DISTINCT entity_type FROM records")
            record_types = sorted(
                {
                    (row["entity_type"] if isinstance(row, sqlite3.Row) else row[0])
                    for row in cursor.fetchall()
                }
            )
        return {
            "order_statuses": order_statuses,
            "record_types": record_types,
        }


_engine_instance: Optional[AnalyticsEngine] = None


def get_analytics_engine() -> AnalyticsEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = AnalyticsEngine()
    return _engine_instance


# ---------------------------------------------------------------------------
# Report runners
# ---------------------------------------------------------------------------


def _run_orders_overview(
    snapshot: DataHarmonySnapshot, params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    start_date: Optional[date] = params.get("start_date")
    end_date: Optional[date] = params.get("end_date")
    statuses = params.get("statuses") or []
    include_deleted = bool(params.get("include_deleted"))
    status_filter = {status.lower() for status in statuses} if statuses else None

    filtered_orders: List[Dict[str, Any]] = []
    monthly_revenue: Dict[str, float] = defaultdict(float)
    monthly_orders: Dict[str, int] = defaultdict(int)
    daily_orders: Dict[str, int] = defaultdict(int)
    status_counts: Counter[str] = Counter()
    customer_metrics: Dict[str, Dict[str, Any]] = {}
    total_revenue = 0.0
    total_items_revenue = 0.0
    total_items_quantity = 0

    for order in snapshot.orders:
        status_value = (order.get("status") or "").strip()
        if status_value.lower() == "deleted" and not include_deleted:
            continue
        if status_filter and status_value.lower() not in status_filter:
            continue
        order_dt = _parse_datetime(order.get("order_date") or order.get("created_at"))
        order_date_value = order_dt.date() if order_dt else None
        if start_date and (order_date_value is None or order_date_value < start_date):
            continue
        if end_date and (order_date_value is None or order_date_value > end_date):
            continue

        filtered_orders.append(order)
        total_amount = _float(order.get("total_amount"))
        total_revenue += total_amount
        status_counts[status_value or "Unspecified"] += 1
        if order_date_value:
            month_key = order_date_value.strftime("%Y-%m")
            monthly_revenue[month_key] += total_amount
            monthly_orders[month_key] += 1
            daily_orders[order_date_value.isoformat()] += 1
        else:
            monthly_revenue["Undated"] += total_amount
            monthly_orders["Undated"] += 1

        order_id = order.get("order_id")
        line_items = snapshot.line_items_by_order.get(str(order_id), [])
        customer_id = order.get("contact_id") or "unassigned"
        metrics = customer_metrics.setdefault(
            str(customer_id),
            {
                "orders": 0,
                "revenue": 0.0,
                "last_order": None,
                "line_items": 0,
            },
        )
        metrics["orders"] += 1
        metrics["revenue"] += total_amount
        metrics["line_items"] += len(line_items)
        if order_date_value and (
            metrics["last_order"] is None or order_date_value > metrics["last_order"]
        ):
            metrics["last_order"] = order_date_value

        for item in line_items:
            quantity = _int(item.get("quantity"))
            price_cents = _int(item.get("price_per_unit_cents"))
            revenue = (quantity * price_cents) / 100.0
            total_items_quantity += quantity
            total_items_revenue += revenue

    order_count = len(filtered_orders)
    average_order_value = total_revenue / order_count if order_count else 0.0
    average_items_per_order = (
        total_items_quantity / order_count if order_count else 0.0
    )

    today = datetime.now(timezone.utc).date()
    trailing_start = today - timedelta(days=30)
    trailing_orders = 0
    for order in filtered_orders:
        order_dt = _parse_datetime(order.get("order_date") or order.get("created_at"))
        if order_dt and order_dt.date() >= trailing_start:
            trailing_orders += 1

    month_count = max(1, len([key for key in monthly_revenue.keys() if key != "Undated"]))
    projected_revenue = (total_revenue / month_count) * 12 if month_count else total_revenue

    processing_durations: List[float] = []
    for order in filtered_orders:
        created = _parse_datetime(order.get("created_at"))
        updated = _parse_datetime(order.get("updated_at"))
        if created and updated and updated >= created:
            processing_durations.append((updated - created).total_seconds())
    avg_processing_seconds = (
        sum(processing_durations) / len(processing_durations)
        if processing_durations
        else 0.0
    )

    summary = [
        _summary_entry("total_revenue", "Total Revenue", total_revenue, format_hint="currency"),
        _summary_entry("order_count", "Orders", float(order_count)),
        _summary_entry(
            "average_order_value",
            "Average Order Value",
            average_order_value,
            format_hint="currency",
        ),
        _summary_entry(
            "items_per_order",
            "Avg. Line Items per Order",
            average_items_per_order,
            description="Average number of line items per order.",
        ),
        _summary_entry(
            "trailing_30_day_orders",
            "Orders (30d)",
            float(trailing_orders),
            description="Orders created in the trailing 30 days.",
        ),
        _summary_entry(
            "projected_revenue",
            "Projected Annual Revenue",
            projected_revenue,
            format_hint="currency",
        ),
        _summary_entry(
            "avg_processing_time",
            "Avg. Processing Time",
            avg_processing_seconds,
            format_hint="duration",
        ),
    ]

    status_breakdown = [
        {"label": status, "value": count}
        for status, count in sorted(status_counts.items(), key=lambda item: item[1], reverse=True)
    ]

    def _month_sort_key(month_key: str) -> Any:
        if month_key == "Undated":
            return (9999, 12)
        year, month = month_key.split("-")
        return (int(year), int(month))

    ordered_months = [key for key in sorted(monthly_revenue.keys(), key=_month_sort_key)]
    monthly_labels = ordered_months
    monthly_dataset = [round(monthly_revenue[key], 2) for key in monthly_labels]
    monthly_orders_dataset = [monthly_orders.get(key, 0) for key in monthly_labels]

    ordered_days = sorted(daily_orders.keys())[-90:]
    daily_dataset = [daily_orders[key] for key in ordered_days]

    charts = []
    if monthly_labels:
        charts.append(
            {
                "id": "revenue_by_month",
                "type": "bar",
                "title": "Revenue by Month",
                "labels": monthly_labels,
                "datasets": [
                    {
                        "label": "Revenue",
                        "data": monthly_dataset,
                        "backgroundColor": "rgba(249, 115, 22, 0.35)",
                        "borderColor": "rgb(249, 115, 22)",
                    },
                    {
                        "label": "Orders",
                        "data": monthly_orders_dataset,
                        "type": "line",
                        "yAxisID": "orders-axis",
                        "borderColor": "rgb(59, 130, 246)",
                        "backgroundColor": "rgba(59, 130, 246, 0.15)",
                    },
                ],
                "options": {
                    "interaction": {"mode": "index", "intersect": False},
                    "scales": {
                        "orders-axis": {"position": "right", "grid": {"drawOnChartArea": False}},
                    },
                },
            }
        )
    if status_breakdown:
        charts.append(
            {
                "id": "status_breakdown",
                "type": "doughnut",
                "title": "Order Status Mix",
                "labels": [entry["label"] for entry in status_breakdown],
                "datasets": [
                    {
                        "label": "Orders",
                        "data": [entry["value"] for entry in status_breakdown],
                    }
                ],
            }
        )
    if ordered_days:
        charts.append(
            {
                "id": "daily_order_volume",
                "type": "line",
                "title": "Daily Order Volume (90d)",
                "labels": ordered_days,
                "datasets": [
                    {
                        "label": "Orders",
                        "data": daily_dataset,
                        "borderColor": "rgb(16, 185, 129)",
                        "backgroundColor": "rgba(16, 185, 129, 0.2)",
                        "tension": 0.3,
                    }
                ],
            }
        )

    top_customers = []
    for customer_id, metrics in customer_metrics.items():
        name = snapshot.resolve_contact_name(customer_id if customer_id != "unassigned" else None)
        avg_value = metrics["revenue"] / metrics["orders"] if metrics["orders"] else 0.0
        top_customers.append(
            {
                "customer": name,
                "contactId": None if customer_id == "unassigned" else customer_id,
                "orders": metrics["orders"],
                "revenue": round(metrics["revenue"], 2),
                "avg_order_value": round(avg_value, 2),
                "last_order": metrics["last_order"].isoformat()
                if metrics["last_order"]
                else None,
            }
        )

    top_customers.sort(key=lambda entry: entry["revenue"], reverse=True)
    tables = [
        {
            "id": "top_customers",
            "title": "Top Customers",
            "columns": [
                {"key": "customer", "label": "Customer"},
                {"key": "orders", "label": "Orders", "format": "number"},
                {"key": "revenue", "label": "Revenue", "format": "currency"},
                {"key": "avg_order_value", "label": "Avg. Order", "format": "currency"},
                {"key": "last_order", "label": "Last Order"},
            ],
            "rows": top_customers[:15],
        }
    ]

    notes = [
        "Orders lacking a total amount contribute $0.00 to revenue figures.",
        "Undated orders are grouped together under the 'Undated' bucket.",
    ]

    meta = {
        "filters": {
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "statuses": statuses,
            "include_deleted": include_deleted,
        },
        "scanCounts": {
            "orders": order_count,
            "lineItems": total_items_quantity,
        },
    }

    return {
        "summary": summary,
        "charts": charts,
        "tables": tables,
        "notes": notes,
        "meta": meta,
        "dataSources": ["orders", "order_line_items", "contacts"],
    }


def _run_line_item_performance(
    snapshot: DataHarmonySnapshot, params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    start_date: Optional[date] = params.get("start_date")
    end_date: Optional[date] = params.get("end_date")
    grouping = params.get("grouping") or "catalog_item"
    top_n = max(1, int(params.get("top_n") or 15))

    orders_lookup = snapshot.orders_by_id

    aggregates: Dict[str, Dict[str, Any]] = {}
    monthly_totals: Dict[str, float] = defaultdict(float)
    overall_revenue = 0.0
    overall_quantity = 0

    for line_item in snapshot.order_line_items:
        order_id = line_item.get("order_id")
        if not order_id or order_id not in orders_lookup:
            continue
        order = orders_lookup[order_id]
        order_dt = _parse_datetime(order.get("order_date") or order.get("created_at"))
        order_date_value = order_dt.date() if order_dt else None
        if start_date and (order_date_value is None or order_date_value < start_date):
            continue
        if end_date and (order_date_value is None or order_date_value > end_date):
            continue

        quantity = _int(line_item.get("quantity"))
        price_cents = _int(line_item.get("price_per_unit_cents"))
        revenue = (quantity * price_cents) / 100.0

        if grouping == "catalog_item":
            key = line_item.get("catalog_item_id") or line_item.get("name") or "uncatalogued"
            label = snapshot.resolve_item_name(line_item.get("catalog_item_id"), fallback=line_item.get("name"))
        elif grouping == "package":
            key = line_item.get("package_id") or "unassigned"
            label = snapshot.resolve_package_name(line_item.get("package_id"))
        else:
            key = str(order_id)
            label = f"Order {order_id}"

        entry = aggregates.setdefault(
            str(key),
            {
                "label": label,
                "orders": set(),
                "quantity": 0,
                "revenue": 0.0,
                "unit_prices": [],
            },
        )
        entry["orders"].add(str(order_id))
        entry["quantity"] += quantity
        entry["revenue"] += revenue
        if price_cents:
            entry["unit_prices"].append(price_cents / 100.0)

        overall_revenue += revenue
        overall_quantity += quantity
        if order_date_value:
            month_key = order_date_value.strftime("%Y-%m")
            monthly_totals[month_key] += revenue

    summary = [
        _summary_entry("revenue", "Line Item Revenue", overall_revenue, format_hint="currency"),
        _summary_entry("quantity", "Units Fulfilled", float(overall_quantity)),
        _summary_entry("unique_groups", "Unique Groups", float(len(aggregates))),
        _summary_entry(
            "avg_unit_price",
            "Avg. Unit Price",
            (overall_revenue / overall_quantity) if overall_quantity else 0.0,
            format_hint="currency",
        ),
    ]

    ranked = []
    for key, entry in aggregates.items():
        avg_unit_price = (
            sum(entry["unit_prices"]) / len(entry["unit_prices"]) if entry["unit_prices"] else 0.0
        )
        ranked.append(
            {
                "group": entry["label"],
                "id": key,
                "orders": len(entry["orders"]),
                "quantity": entry["quantity"],
                "revenue": round(entry["revenue"], 2),
                "avg_unit_price": round(avg_unit_price, 2),
            }
        )

    ranked.sort(key=lambda entry: entry["revenue"], reverse=True)

    labels = [row["group"] for row in ranked[:top_n]]
    revenue_data = [row["revenue"] for row in ranked[:top_n]]
    quantity_data = [row["quantity"] for row in ranked[:top_n]]

    ordered_months = sorted(monthly_totals.keys())
    charts = []
    if labels:
        charts.append(
            {
                "id": "top_groups_revenue",
                "type": "bar",
                "title": "Top Groups by Revenue",
                "labels": labels,
                "datasets": [
                    {
                        "label": "Revenue",
                        "data": revenue_data,
                        "backgroundColor": "rgba(79, 70, 229, 0.4)",
                        "borderColor": "rgb(79, 70, 229)",
                    }
                ],
            }
        )
        charts.append(
            {
                "id": "top_groups_quantity",
                "type": "bar",
                "title": "Top Groups by Quantity",
                "labels": labels,
                "datasets": [
                    {
                        "label": "Quantity",
                        "data": quantity_data,
                        "backgroundColor": "rgba(14, 165, 233, 0.35)",
                        "borderColor": "rgb(14, 165, 233)",
                    }
                ],
            }
        )
    if ordered_months:
        charts.append(
            {
                "id": "line_item_revenue_trend",
                "type": "line",
                "title": "Line Item Revenue Trend",
                "labels": ordered_months,
                "datasets": [
                    {
                        "label": "Revenue",
                        "data": [monthly_totals[key] for key in ordered_months],
                        "borderColor": "rgb(249, 115, 22)",
                        "backgroundColor": "rgba(249, 115, 22, 0.2)",
                        "tension": 0.35,
                    }
                ],
            }
        )

    tables = [
        {
            "id": "line_item_groups",
            "title": "Group Breakdown",
            "columns": [
                {"key": "group", "label": "Group"},
                {"key": "orders", "label": "Orders", "format": "number"},
                {"key": "quantity", "label": "Quantity", "format": "number"},
                {"key": "revenue", "label": "Revenue", "format": "currency"},
                {"key": "avg_unit_price", "label": "Avg. Unit Price", "format": "currency"},
            ],
            "rows": ranked[: max(top_n, 25)],
        }
    ]

    meta = {
        "filters": {
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "grouping": grouping,
        },
        "scanCounts": {
            "lineItems": len(snapshot.order_line_items),
            "orders": len(orders_lookup),
        },
    }

    notes = [
        "Revenue is derived from price per unit multiplied by quantity for each line item.",
        "Orders missing catalog references are grouped using the line item name.",
    ]

    return {
        "summary": summary,
        "charts": charts,
        "tables": tables,
        "notes": notes,
        "meta": meta,
        "dataSources": ["order_line_items", "orders", "items", "packages"],
    }


def _run_customer_performance(
    snapshot: DataHarmonySnapshot, params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    start_date: Optional[date] = params.get("start_date")
    end_date: Optional[date] = params.get("end_date")
    minimum_orders = max(1, int(params.get("minimum_orders") or 1))
    top_n = max(1, int(params.get("top_n") or 10))

    aggregates: Dict[str, Dict[str, Any]] = {}
    total_revenue = 0.0

    for order in snapshot.orders:
        order_dt = _parse_datetime(order.get("order_date") or order.get("created_at"))
        order_date_value = order_dt.date() if order_dt else None
        if start_date and (order_date_value is None or order_date_value < start_date):
            continue
        if end_date and (order_date_value is None or order_date_value > end_date):
            continue
        contact_id = order.get("contact_id") or "unassigned"
        entry = aggregates.setdefault(
            str(contact_id),
            {
                "orders": 0,
                "revenue": 0.0,
                "last_order": None,
                "first_order": None,
            },
        )
        entry["orders"] += 1
        amount = _float(order.get("total_amount"))
        entry["revenue"] += amount
        total_revenue += amount
        if order_date_value:
            if entry["last_order"] is None or order_date_value > entry["last_order"]:
                entry["last_order"] = order_date_value
            if entry["first_order"] is None or order_date_value < entry["first_order"]:
                entry["first_order"] = order_date_value

    results = []
    for contact_id, metrics in aggregates.items():
        if metrics["orders"] < minimum_orders:
            continue
        name = snapshot.resolve_contact_name(contact_id if contact_id != "unassigned" else None)
        avg_value = metrics["revenue"] / metrics["orders"] if metrics["orders"] else 0.0
        cycle_days = 0.0
        if metrics["first_order"] and metrics["last_order"] and metrics["first_order"] != metrics["last_order"]:
            days_between = (metrics["last_order"] - metrics["first_order"]).days
            cycle_days = days_between / max(1, metrics["orders"] - 1)
        results.append(
            {
                "customer": name,
                "contactId": None if contact_id == "unassigned" else contact_id,
                "orders": metrics["orders"],
                "revenue": round(metrics["revenue"], 2),
                "avg_order_value": round(avg_value, 2),
                "order_cycle_days": round(cycle_days, 2) if cycle_days else None,
                "first_order": metrics["first_order"].isoformat()
                if metrics["first_order"]
                else None,
                "last_order": metrics["last_order"].isoformat() if metrics["last_order"] else None,
            }
        )

    results.sort(key=lambda entry: entry["revenue"], reverse=True)
    leading = results[:top_n]

    top_labels = [row["customer"] for row in leading]
    revenue_data = [row["revenue"] for row in leading]
    orders_data = [row["orders"] for row in leading]

    summary = [
        _summary_entry("total_revenue", "Total Revenue", total_revenue, format_hint="currency"),
        _summary_entry("customer_count", "Customers", float(len(results))),
        _summary_entry(
            "top_customer_value",
            "Top Customer",
            leading[0]["revenue"] if leading else 0.0,
            format_hint="currency",
            description=leading[0]["customer"] if leading else "",
        ),
        _summary_entry(
            "avg_revenue_per_customer",
            "Avg. Revenue / Customer",
            (total_revenue / len(results)) if results else 0.0,
            format_hint="currency",
        ),
    ]

    charts = []
    if top_labels:
        charts.append(
            {
                "id": "customer_revenue",
                "type": "bar",
                "title": "Revenue by Customer",
                "labels": top_labels,
                "datasets": [
                    {
                        "label": "Revenue",
                        "data": revenue_data,
                        "backgroundColor": "rgba(249, 115, 22, 0.4)",
                        "borderColor": "rgb(249, 115, 22)",
                    }
                ],
            }
        )
        charts.append(
            {
                "id": "customer_orders",
                "type": "line",
                "title": "Order Volume by Customer",
                "labels": top_labels,
                "datasets": [
                    {
                        "label": "Orders",
                        "data": orders_data,
                        "borderColor": "rgb(59, 130, 246)",
                        "backgroundColor": "rgba(59, 130, 246, 0.2)",
                    }
                ],
            }
        )

    tables = [
        {
            "id": "customer_performance",
            "title": "Customer Performance",
            "columns": [
                {"key": "customer", "label": "Customer"},
                {"key": "orders", "label": "Orders", "format": "number"},
                {"key": "revenue", "label": "Revenue", "format": "currency"},
                {"key": "avg_order_value", "label": "Avg. Order", "format": "currency"},
                {"key": "order_cycle_days", "label": "Cycle (days)"},
                {"key": "first_order", "label": "First Order"},
                {"key": "last_order", "label": "Most Recent"},
            ],
            "rows": leading,
        }
    ]

    meta = {
        "filters": {
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "minimum_orders": minimum_orders,
            "top_n": top_n,
        },
    }

    notes = [
        "Customers without linked contacts are grouped under 'Unassigned'.",
        "Cycle time approximates average days between orders for repeat customers.",
    ]

    return {
        "summary": summary,
        "charts": charts,
        "tables": tables,
        "notes": notes,
        "meta": meta,
        "dataSources": ["orders", "contacts"],
    }


def _run_reminder_health(
    snapshot: DataHarmonySnapshot, params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    horizon_days = max(1, int(params.get("days_ahead") or 14))
    timezone_name = snapshot.timezone or "UTC"
    tzinfo = _safe_timezone(timezone_name) or timezone.utc
    now_dt = datetime.now(tzinfo)
    today = now_dt.date()
    horizon_date = today + timedelta(days=horizon_days)

    reminders = snapshot.get_records("reminder")
    totals = {
        "total": 0,
        "completed": 0,
        "overdue": 0,
        "upcoming": 0,
        "unscheduled": 0,
    }
    by_month: Dict[str, Dict[str, int]] = defaultdict(lambda: {"completed": 0, "scheduled": 0})
    upcoming_rows: List[Dict[str, Any]] = []
    overdue_rows: List[Dict[str, Any]] = []

    for reminder in reminders:
        totals["total"] += 1
        completed = bool(reminder.get("completed"))
        due_dt = _parse_datetime(reminder.get("due_at"))
        due_date = due_dt.date() if due_dt else None
        month_key = due_date.strftime("%Y-%m") if due_date else "Unscheduled"
        if completed:
            totals["completed"] += 1
            by_month[month_key]["completed"] += 1
        else:
            by_month[month_key]["scheduled"] += 1
        if not due_date:
            totals["unscheduled"] += 1
            continue
        if not completed and due_date < today:
            totals["overdue"] += 1
            overdue_rows.append(
                {
                    "title": reminder.get("title"),
                    "handle": reminder.get("handle"),
                    "due_date": due_dt.isoformat() if due_dt else None,
                    "notes": reminder.get("notes"),
                }
            )
        elif not completed and today <= due_date <= horizon_date:
            totals["upcoming"] += 1
            upcoming_rows.append(
                {
                    "title": reminder.get("title"),
                    "handle": reminder.get("handle"),
                    "due_date": due_dt.isoformat() if due_dt else None,
                    "notes": reminder.get("notes"),
                }
            )

    summary = [
        _summary_entry("total", "Total Reminders", float(totals["total"])),
        _summary_entry("completed", "Completed", float(totals["completed"])),
        _summary_entry("overdue", "Overdue", float(totals["overdue"])),
        _summary_entry("upcoming", "Due Soon", float(totals["upcoming"])),
    ]

    status_chart = {
        "id": "reminder_status_mix",
        "type": "doughnut",
        "title": "Reminder Status Mix",
        "labels": ["Completed", "Overdue", "Upcoming", "Unscheduled"],
        "datasets": [
            {
                "label": "Reminders",
                "data": [
                    totals["completed"],
                    totals["overdue"],
                    totals["upcoming"],
                    totals["unscheduled"],
                ],
            }
        ],
    }

    ordered_months = sorted(by_month.keys())
    monthly_chart = {
        "id": "reminders_by_month",
        "type": "bar",
        "title": "Reminders by Month",
        "labels": ordered_months,
        "datasets": [
            {
                "label": "Scheduled",
                "data": [by_month[key]["scheduled"] for key in ordered_months],
                "backgroundColor": "rgba(59, 130, 246, 0.35)",
                "borderColor": "rgb(59, 130, 246)",
            },
            {
                "label": "Completed",
                "data": [by_month[key]["completed"] for key in ordered_months],
                "backgroundColor": "rgba(16, 185, 129, 0.3)",
                "borderColor": "rgb(16, 185, 129)",
            },
        ],
    }

    charts = [status_chart]
    if ordered_months:
        charts.append(monthly_chart)

    upcoming_rows.sort(key=lambda entry: entry["due_date"] or "")
    overdue_rows.sort(key=lambda entry: entry["due_date"] or "")

    tables = []
    if upcoming_rows:
        tables.append(
            {
                "id": "upcoming_reminders",
                "title": f"Upcoming Reminders (next {horizon_days} days)",
                "columns": [
                    {"key": "title", "label": "Reminder"},
                    {"key": "handle", "label": "Handle"},
                    {"key": "due_date", "label": "Due"},
                    {"key": "notes", "label": "Notes"},
                ],
                "rows": upcoming_rows[:25],
            }
        )
    if overdue_rows:
        tables.append(
            {
                "id": "overdue_reminders",
                "title": "Overdue Reminders",
                "columns": [
                    {"key": "title", "label": "Reminder"},
                    {"key": "handle", "label": "Handle"},
                    {"key": "due_date", "label": "Due"},
                    {"key": "notes", "label": "Notes"},
                ],
                "rows": overdue_rows[:25],
            }
        )

    notes = [
        "Reminders without a due date are tracked under the 'Unscheduled' category.",
        "Completion percentages are calculated against the total reminder count.",
    ]

    meta = {
        "filters": {"days_ahead": horizon_days},
        "timezone": timezone_name,
        "scanCounts": {"reminders": len(reminders)},
    }

    return {
        "summary": summary,
        "charts": charts,
        "tables": tables,
        "notes": notes,
        "meta": meta,
        "dataSources": ["records:reminder"],
    }


def _run_records_activity(
    snapshot: DataHarmonySnapshot, params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    records_by_type = {entity: snapshot.get_records(entity) for entity in snapshot.records.keys()}
    record_counts = {entity: len(records) for entity, records in records_by_type.items()}
    mention_counts: Dict[str, int] = defaultdict(int)
    for mention in snapshot.record_mentions:
        entity = mention.get("mentioned_entity_type")
        if entity:
            mention_counts[entity] += 1
    activity_counts: Dict[str, Counter[str]] = defaultdict(Counter)
    for entry in snapshot.record_activity:
        entity = entry.get("entity_type")
        action = entry.get("action") or "other"
        if entity:
            activity_counts[entity][action] += 1

    total_records = sum(record_counts.values())
    total_mentions = sum(mention_counts.values())
    total_actions = sum(sum(counter.values()) for counter in activity_counts.values())

    summary = [
        _summary_entry("record_types", "Record Types", float(len(record_counts))),
        _summary_entry("records", "Records", float(total_records)),
        _summary_entry("mentions", "Mentions", float(total_mentions)),
        _summary_entry("activity", "Activity Entries", float(total_actions)),
    ]

    labels = sorted(record_counts.keys())
    record_data = [record_counts[label] for label in labels]
    mention_data = [mention_counts.get(label, 0) for label in labels]

    charts = []
    if labels:
        charts.append(
            {
                "id": "records_by_type",
                "type": "bar",
                "title": "Records by Type",
                "labels": labels,
                "datasets": [
                    {
                        "label": "Records",
                        "data": record_data,
                        "backgroundColor": "rgba(34, 197, 94, 0.35)",
                        "borderColor": "rgb(34, 197, 94)",
                    }
                ],
            }
        )
        charts.append(
            {
                "id": "mentions_by_type",
                "type": "bar",
                "title": "Mentions by Type",
                "labels": labels,
                "datasets": [
                    {
                        "label": "Mentions",
                        "data": mention_data,
                        "backgroundColor": "rgba(59, 130, 246, 0.3)",
                        "borderColor": "rgb(59, 130, 246)",
                    }
                ],
            }
        )

    table_rows = []
    for label in labels:
        actions = activity_counts.get(label, Counter())
        table_rows.append(
            {
                "entity": label,
                "records": record_counts.get(label, 0),
                "mentions": mention_counts.get(label, 0),
                "activity": sum(actions.values()),
                "top_action": actions.most_common(1)[0][0] if actions else None,
            }
        )

    tables = [
        {
            "id": "record_type_activity",
            "title": "Record Activity",
            "columns": [
                {"key": "entity", "label": "Entity Type"},
                {"key": "records", "label": "Records", "format": "number"},
                {"key": "mentions", "label": "Mentions", "format": "number"},
                {"key": "activity", "label": "Activity Entries", "format": "number"},
                {"key": "top_action", "label": "Top Action"},
            ],
            "rows": table_rows,
        }
    ]

    notes = [
        "Includes all record-backed entities managed via Data Harmony.",
        "Activity counts aggregate all logged actions regardless of actor.",
    ]

    meta = {
        "recordTypes": labels,
        "scanCounts": {
            "records": total_records,
            "record_mentions": len(snapshot.record_mentions),
            "record_activity": len(snapshot.record_activity),
        },
    }

    return {
        "summary": summary,
        "charts": charts,
        "tables": tables,
        "notes": notes,
        "meta": meta,
        "dataSources": ["records", "record_mentions", "record_activity_logs"],
    }


def _build_dataset_options(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    base_options = [
        {"value": "orders", "label": "Orders"},
        {"value": "order_line_items", "label": "Order Line Items"},
        {"value": "order_logs", "label": "Order Logs"},
        {"value": "order_status_history", "label": "Order Status History"},
        {"value": "contacts", "label": "Contacts"},
        {"value": "items", "label": "Items"},
        {"value": "packages", "label": "Packages"},
        {"value": "package_items", "label": "Package Items"},
        {"value": "record_mentions", "label": "Record Mentions"},
        {"value": "record_activity_logs", "label": "Record Activity"},
        {"value": "record_handles", "label": "Record Handles"},
        {"value": "reminders", "label": "Reminders"},
        {"value": "calendar_events", "label": "Calendar Events"},
    ]
    record_types = context.get("record_types") or []
    for entity in record_types:
        base_options.append({"value": f"records:{entity}", "label": f"Records · {entity}"})
    return base_options


def _run_dataset_overview(
    snapshot: DataHarmonySnapshot, params: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    dataset_name = params.get("dataset") or "orders"
    sample_size = max(1, int(params.get("sample_size") or 10))
    records = snapshot.get_dataset(dataset_name)

    field_counter: Counter[str] = Counter()
    created_dates: List[datetime] = []
    updated_dates: List[datetime] = []

    for record in records:
        if isinstance(record, dict):
            field_counter.update(record.keys())
            created_dates.append(_parse_datetime(record.get("created_at")))
            updated_dates.append(_parse_datetime(record.get("updated_at")))

    earliest = min((dt for dt in created_dates if dt), default=None)
    latest = max((dt for dt in updated_dates if dt), default=None)

    summary = [
        _summary_entry("records", "Records", float(len(records))),
        _summary_entry(
            "fields", "Unique Fields", float(len(field_counter)), description="Distinct keys seen in the dataset."
        ),
        _summary_entry(
            "latest",
            "Most Recent Update",
            (latest - earliest).total_seconds() if earliest and latest else 0.0,
            format_hint="duration",
            description="Duration between earliest creation and latest update.",
        ),
    ]

    most_common_fields = field_counter.most_common(12)
    charts = []
    if most_common_fields:
        charts.append(
            {
                "id": "field_frequency",
                "type": "bar",
                "title": "Most Common Fields",
                "labels": [field for field, _ in most_common_fields],
                "datasets": [
                    {
                        "label": "Occurrences",
                        "data": [count for _, count in most_common_fields],
                        "backgroundColor": "rgba(148, 163, 184, 0.35)",
                        "borderColor": "rgb(148, 163, 184)",
                    }
                ],
            }
        )

    sample_rows = []
    for record in records[:sample_size]:
        row_copy = {}
        if isinstance(record, dict):
            for key, value in list(record.items())[:10]:
                row_copy[key] = value
        else:
            row_copy["value"] = record
        sample_rows.append(row_copy)

    columns = []
    if sample_rows:
        keys = set()
        for row in sample_rows:
            keys.update(row.keys())
        columns = [{"key": key, "label": key.replace("_", " ").title()} for key in list(keys)[:10]]

    tables = []
    if columns:
        tables.append(
            {
                "id": "dataset_sample",
                "title": "Sample Rows",
                "columns": columns,
                "rows": sample_rows,
            }
        )

    notes = [
        "Sample rows are truncated to the first 10 fields for readability.",
        "Duration metric compares earliest creation timestamp to latest update timestamp when available.",
    ]

    meta = {
        "dataset": dataset_name,
        "earliest": earliest.isoformat() if earliest else None,
        "latest": latest.isoformat() if latest else None,
    }

    return {
        "summary": summary,
        "charts": charts,
        "tables": tables,
        "notes": notes,
        "meta": meta,
        "dataSources": [dataset_name],
    }


__all__ = ["AnalyticsEngine", "get_analytics_engine"]

