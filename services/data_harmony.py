"""Utilities for synthesising analytics-friendly snapshots from the data layer."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return ``True`` if the table exists in the connected database."""

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    """Normalise sqlite rows to plain dictionaries."""

    normalised: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            normalised.append({key: row[key] for key in row.keys()})
        else:
            normalised.append(dict(row))
    return normalised


def _fetch_table(
    conn: sqlite3.Connection, table_name: str, columns: str = "*"
) -> List[Dict[str, Any]]:
    """Fetch all rows from ``table_name`` as dictionaries.

    Missing tables are treated as empty datasets so that analytics callers can be
    defensive by default.
    """

    if not _table_exists(conn, table_name):
        return []
    cursor = conn.execute(f"SELECT {columns} FROM {table_name}")
    return _rows_to_dicts(cursor.fetchall())


@dataclass
class DataHarmonySnapshot:
    """Container bundling together heterogeneous datasets for analytics.

    The snapshot is intentionally lightweight and read-only: each attribute is a
    list of dictionaries or, for the records collection, a mapping from entity
    types to lists of dictionaries.  Helper properties expose frequently used
    lookup tables so analytics modules can be expressive without re-querying the
    database.
    """

    timezone: str = "UTC"
    orders: List[Dict[str, Any]] = field(default_factory=list)
    order_line_items: List[Dict[str, Any]] = field(default_factory=list)
    order_logs: List[Dict[str, Any]] = field(default_factory=list)
    order_status_history: List[Dict[str, Any]] = field(default_factory=list)
    contacts: List[Dict[str, Any]] = field(default_factory=list)
    items: List[Dict[str, Any]] = field(default_factory=list)
    packages: List[Dict[str, Any]] = field(default_factory=list)
    package_items: List[Dict[str, Any]] = field(default_factory=list)
    record_mentions: List[Dict[str, Any]] = field(default_factory=list)
    record_activity: List[Dict[str, Any]] = field(default_factory=list)
    record_handles: List[Dict[str, Any]] = field(default_factory=list)
    records: Mapping[str, List[Dict[str, Any]]] = field(default_factory=dict)

    _contacts_by_id: Optional[Dict[str, Dict[str, Any]]] = field(
        init=False, default=None, repr=False
    )
    _items_by_id: Optional[Dict[str, Dict[str, Any]]] = field(
        init=False, default=None, repr=False
    )
    _packages_by_id: Optional[Dict[str, Dict[str, Any]]] = field(
        init=False, default=None, repr=False
    )
    _orders_by_id: Optional[Dict[str, Dict[str, Any]]] = field(
        init=False, default=None, repr=False
    )
    _line_items_by_order: Optional[Dict[str, List[Dict[str, Any]]]] = field(
        init=False, default=None, repr=False
    )

    @classmethod
    def build(cls, conn: sqlite3.Connection, *, timezone: str = "UTC") -> "DataHarmonySnapshot":
        """Assemble a snapshot from the underlying SQLite database."""

        snapshot = cls(timezone=timezone)
        snapshot.orders = _fetch_table(conn, "orders")
        snapshot.order_line_items = _fetch_table(conn, "order_line_items")
        snapshot.order_logs = _fetch_table(conn, "order_logs")
        snapshot.order_status_history = _fetch_table(conn, "order_status_history")
        snapshot.contacts = _fetch_table(conn, "contacts")
        snapshot.items = _fetch_table(conn, "items")
        snapshot.packages = _fetch_table(conn, "packages")
        snapshot.package_items = _fetch_table(conn, "package_items")
        snapshot.record_mentions = _fetch_table(conn, "record_mentions")
        snapshot.record_activity = _fetch_table(conn, "record_activity_logs")
        snapshot.record_handles = _fetch_table(conn, "record_handles")

        if _table_exists(conn, "records"):
            cursor = conn.execute(
                "SELECT entity_type, entity_id, data, created_at, updated_at FROM records"
            )
            records_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for row in cursor.fetchall():
                if isinstance(row, sqlite3.Row):
                    entity_type = row["entity_type"]
                    entity_id = row["entity_id"]
                    data_blob = row["data"]
                    created_at = row["created_at"]
                    updated_at = row["updated_at"]
                else:
                    entity_type = row[0]
                    entity_id = row[1]
                    data_blob = row[2]
                    created_at = row[3] if len(row) > 3 else None
                    updated_at = row[4] if len(row) > 4 else None
                payload: Dict[str, Any]
                try:
                    payload = json.loads(data_blob) if data_blob else {}
                except json.JSONDecodeError:
                    payload = {}
                payload.setdefault("id", entity_id)
                if created_at is not None:
                    payload.setdefault("created_at", created_at)
                if updated_at is not None:
                    payload.setdefault("updated_at", updated_at)
                records_map[entity_type].append(payload)
            snapshot.records = dict(records_map)
        else:
            snapshot.records = {}

        return snapshot

    # ------------------------------------------------------------------
    # Derived lookups
    # ------------------------------------------------------------------
    @property
    def contacts_by_id(self) -> Dict[str, Dict[str, Any]]:
        if self._contacts_by_id is None:
            self._contacts_by_id = {
                contact.get("id"): contact
                for contact in self.contacts
                if contact.get("id") is not None
            }
        return self._contacts_by_id

    @property
    def items_by_id(self) -> Dict[str, Dict[str, Any]]:
        if self._items_by_id is None:
            self._items_by_id = {
                item.get("id"): item for item in self.items if item.get("id") is not None
            }
        return self._items_by_id

    @property
    def packages_by_id(self) -> Dict[str, Dict[str, Any]]:
        if self._packages_by_id is None:
            self._packages_by_id = {
                str(pkg.get("package_id")): pkg
                for pkg in self.packages
                if pkg.get("package_id") is not None
            }
        return self._packages_by_id

    @property
    def orders_by_id(self) -> Dict[str, Dict[str, Any]]:
        if self._orders_by_id is None:
            self._orders_by_id = {
                order.get("order_id"): order
                for order in self.orders
                if order.get("order_id") is not None
            }
        return self._orders_by_id

    @property
    def line_items_by_order(self) -> Mapping[str, List[Dict[str, Any]]]:
        if self._line_items_by_order is None:
            mapping: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for line_item in self.order_line_items:
                order_id = line_item.get("order_id")
                if order_id is None:
                    continue
                mapping[str(order_id)].append(line_item)
            self._line_items_by_order = mapping
        return self._line_items_by_order

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------
    def get_records(self, entity_type: str) -> List[Dict[str, Any]]:
        return list(self.records.get(entity_type, []))

    def order_statuses(self) -> List[str]:
        statuses = set()
        for order in self.orders:
            status = (order.get("status") or "").strip()
            if status:
                statuses.add(status)
        return sorted(statuses)

    def record_entity_types(self) -> List[str]:
        return sorted(self.records.keys())

    def resolve_contact_name(self, contact_id: Optional[str]) -> str:
        if not contact_id:
            return "Unassigned"
        contact = self.contacts_by_id.get(contact_id)
        if not contact:
            return str(contact_id)
        for key in ("company_name", "contact_name", "email", "id"):
            value = contact.get(key)
            if value:
                return str(value)
        return str(contact_id)

    def resolve_contact(self, contact_id: Optional[str]) -> Dict[str, Any]:
        return self.contacts_by_id.get(contact_id or "", {})

    def resolve_item_name(self, item_id: Optional[str], fallback: Optional[str] = None) -> str:
        if item_id and item_id in self.items_by_id:
            item = self.items_by_id[item_id]
            for key in ("name", "id"):
                value = item.get(key)
                if value:
                    return str(value)
        if fallback:
            return str(fallback)
        if item_id:
            return str(item_id)
        return "Uncatalogued"

    def resolve_package_name(self, package_id: Optional[str]) -> str:
        if package_id and str(package_id) in self.packages_by_id:
            package = self.packages_by_id[str(package_id)]
            if package.get("name"):
                return str(package["name"])
        if package_id:
            return str(package_id)
        return "Unassigned"

    def get_dataset(self, dataset: str) -> List[Dict[str, Any]]:
        dataset = dataset.lower()
        if dataset == "orders":
            return list(self.orders)
        if dataset == "order_line_items":
            return list(self.order_line_items)
        if dataset == "order_logs":
            return list(self.order_logs)
        if dataset == "order_status_history":
            return list(self.order_status_history)
        if dataset == "contacts":
            return list(self.contacts)
        if dataset == "items":
            return list(self.items)
        if dataset == "packages":
            return list(self.packages)
        if dataset == "package_items":
            return list(self.package_items)
        if dataset == "record_mentions":
            return list(self.record_mentions)
        if dataset == "record_activity_logs" or dataset == "record_activity":
            return list(self.record_activity)
        if dataset == "record_handles":
            return list(self.record_handles)
        if dataset.startswith("records:"):
            _, _, entity = dataset.partition(":")
            return self.get_records(entity)
        if dataset == "reminders":
            return self.get_records("reminder")
        if dataset == "calendar_events":
            return self.get_records("calendar_event")
        return []


__all__ = ["DataHarmonySnapshot", "_table_exists"]

