"""Tools for converting legacy FireCoast backups into the modern format.

The analytics "data harmony" work introduced a more structured backup layout
that revolves around a consolidated SQLite database.  Older backups, however,
could be almost anything: JSON exports, loose SQLite files, or ad-hoc ZIP
archives with a sprinkling of useful documents.  This module performs a
best-effort conversion of those legacy artefacts into a fresh backup archive
that the current import pipeline can understand.

The goal of the converter is to be resilient rather than perfect.  Whenever we
find structured information we attempt to map it to the new schema.  Any files
we do not understand are still included in the resulting archive under a
``legacy_assets`` folder so that nothing is lost during the migration.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional
from zipfile import ZipFile

LOGGER = logging.getLogger(__name__)

JSON_EXTENSIONS = {".json", ".jsonl", ".geojson"}
DATABASE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}


# ---------------------------------------------------------------------------
# Dataclasses describing the normalised legacy payload
# ---------------------------------------------------------------------------


@dataclass
class LegacyDataset:
    """Normalised representation of the information gleaned from a legacy backup."""

    timezone: str = "UTC"
    settings: Dict[str, Any] = field(default_factory=dict)
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
    records: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    attachments: Dict[str, bytes] = field(default_factory=dict)
    database_blob: Optional[bytes] = None
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_legacy_backup(source: Path, destination_dir: Optional[Path] = None) -> Path:
    """Convert *source* into a FireCoast backup archive.

    Parameters
    ----------
    source:
        Either a file or a directory that represents the legacy payload.  JSON
        files, ZIP archives, SQLite databases and plain directories are all
        supported.

    destination_dir:
        Optional directory where the resulting archive should be written.  When
        omitted a temporary directory adjacent to the source is used.
    """

    source = Path(source).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Legacy source '{source}' does not exist")

    dataset = _ingest_source(source)

    if destination_dir is None:
        destination_dir = source.parent
    destination_dir = Path(destination_dir).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)

    archive_path = destination_dir / f"legacy_migration_{uuid.uuid4().hex}.zip"

    with tempfile.TemporaryDirectory(prefix="firecoast_legacy_") as tmp_dir:
        temp_root = Path(tmp_dir)
        _materialise_dataset(dataset, temp_root)
        _write_archive(temp_root, archive_path)

    LOGGER.info("Created legacy backup archive at %s", archive_path)
    return archive_path


def main(argv: Optional[Iterable[str]] = None) -> int:
    """Command line entry point used by ``python -m services.legacy_backup``."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Path to the legacy backup input")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory where the converted archive should be written",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    archive = build_legacy_backup(args.source, destination_dir=args.output_dir)
    print(archive)
    return 0


# ---------------------------------------------------------------------------
# Ingestion helpers
# ---------------------------------------------------------------------------


def _ingest_source(source: Path) -> LegacyDataset:
    if source.is_dir():
        return _ingest_directory(source)
    if source.suffix.lower() == ".zip":
        return _ingest_zip(source)
    if source.suffix.lower() in JSON_EXTENSIONS:
        return _ingest_json_file(source)
    if source.suffix.lower() in DATABASE_EXTENSIONS:
        dataset = LegacyDataset()
        dataset.database_blob = source.read_bytes()
        dataset.notes.append(f"Imported database file {source.name}")
        return dataset

    dataset = LegacyDataset()
    dataset.attachments[source.name] = source.read_bytes()
    dataset.notes.append(f"Unrecognised file '{source.name}' copied as attachment")
    return dataset


def _ingest_directory(directory: Path) -> LegacyDataset:
    dataset = LegacyDataset()
    for entry in sorted(directory.rglob("*")):
        if entry.is_dir():
            continue
        relative = entry.relative_to(directory).as_posix()
        suffix = entry.suffix.lower()
        try:
            if suffix in JSON_EXTENSIONS:
                _merge_dataset(dataset, _ingest_json_payload(entry.name, entry.read_text()))
            elif suffix == ".zip":
                _merge_dataset(dataset, _ingest_zip(entry))
            elif suffix in DATABASE_EXTENSIONS:
                if dataset.database_blob is None:
                    dataset.database_blob = entry.read_bytes()
                    dataset.notes.append(
                        f"Included database file {relative} as orders_manager.db"
                    )
                else:
                    dataset.attachments[relative] = entry.read_bytes()
                    dataset.notes.append(
                        f"Additional database file {relative} preserved as attachment"
                    )
            else:
                dataset.attachments[relative] = entry.read_bytes()
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.warning("Failed to ingest %s: %s", relative, exc)
            dataset.notes.append(f"Failed to parse {relative}: {exc}")
    return dataset


def _ingest_zip(archive_path: Path) -> LegacyDataset:
    dataset = LegacyDataset()
    with ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            suffix = Path(info.filename).suffix.lower()
            try:
                with archive.open(info) as handle:
                    data = handle.read()
                if suffix in JSON_EXTENSIONS:
                    _merge_dataset(dataset, _ingest_json_payload(info.filename, data.decode("utf-8")))
                elif suffix in DATABASE_EXTENSIONS and dataset.database_blob is None:
                    dataset.database_blob = data
                    dataset.notes.append(
                        f"Included database file {info.filename} as orders_manager.db"
                    )
                else:
                    dataset.attachments[info.filename] = data
            except UnicodeDecodeError:
                dataset.attachments[info.filename] = data
            except Exception as exc:  # pragma: no cover - defensive logging
                LOGGER.warning("Failed to ingest %s from ZIP: %s", info.filename, exc)
                dataset.notes.append(f"Failed to parse {info.filename}: {exc}")
    return dataset


def _ingest_json_file(path: Path) -> LegacyDataset:
    return _ingest_json_payload(path.name, path.read_text())


def _ingest_json_payload(name: str, payload: str) -> LegacyDataset:
    dataset = LegacyDataset()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        dataset.notes.append(f"Could not decode JSON file {name}: {exc}")
        return dataset

    _walk_json_payload(data, dataset)
    return dataset


def _merge_dataset(target: LegacyDataset, other: LegacyDataset) -> None:
    if not other:
        return
    target.settings.update(other.settings)
    if other.timezone:
        target.timezone = other.timezone
    target.orders.extend(other.orders)
    target.order_line_items.extend(other.order_line_items)
    target.order_logs.extend(other.order_logs)
    target.order_status_history.extend(other.order_status_history)
    target.contacts.extend(other.contacts)
    target.items.extend(other.items)
    target.packages.extend(other.packages)
    target.package_items.extend(other.package_items)
    target.record_mentions.extend(other.record_mentions)
    target.record_activity.extend(other.record_activity)
    target.record_handles.extend(other.record_handles)
    if other.records:
        for key, value in other.records.items():
            target.records.setdefault(key, []).extend(value)
    target.attachments.update({k: v for k, v in other.attachments.items() if k not in target.attachments})
    if other.database_blob and not target.database_blob:
        target.database_blob = other.database_blob
    target.notes.extend(other.notes)


# ---------------------------------------------------------------------------
# JSON normalisation
# ---------------------------------------------------------------------------


_SETTINGS_KEYS = {"settings", "config", "configuration", "preferences"}
_ORDERS_KEYS = {"orders", "order_list", "orderhistory", "purchases"}
_LINE_ITEM_KEYS = {"order_line_items", "line_items", "order_items"}
_ORDER_LOG_KEYS = {"order_logs", "logs"}
_ORDER_STATUS_KEYS = {"order_status_history", "status_history", "statusHistory"}
_CONTACT_KEYS = {"contacts", "vendors", "customers"}
_ITEM_KEYS = {"items", "inventory", "products"}
_PACKAGE_KEYS = {"packages", "kits"}
_PACKAGE_ITEM_KEYS = {"package_items", "kit_items"}
_RECORD_KEYS = {"records", "record_entries"}
_RECORD_HANDLE_KEYS = {"record_handles", "handles"}
_RECORD_ACTIVITY_KEYS = {"record_activity", "record_activity_logs", "activity"}
_RECORD_MENTION_KEYS = {"record_mentions", "mentions"}


def _walk_json_payload(node: Any, dataset: LegacyDataset) -> None:
    if isinstance(node, Mapping):
        lower_keys = {str(key).lower(): key for key in node.keys()}

        for canonical_key in _SETTINGS_KEYS:
            if canonical_key in lower_keys and isinstance(node[lower_keys[canonical_key]], Mapping):
                dataset.settings.update(dict(node[lower_keys[canonical_key]]))
                timezone = node[lower_keys[canonical_key]].get("timezone")
                if isinstance(timezone, str):
                    dataset.timezone = timezone

        timezone_value = node.get("timezone") or node.get("time_zone")
        if isinstance(timezone_value, str):
            dataset.timezone = timezone_value

        for key_set, sink in [
            (_ORDERS_KEYS, dataset.orders),
            (_LINE_ITEM_KEYS, dataset.order_line_items),
            (_ORDER_LOG_KEYS, dataset.order_logs),
            (_ORDER_STATUS_KEYS, dataset.order_status_history),
            (_CONTACT_KEYS, dataset.contacts),
            (_ITEM_KEYS, dataset.items),
            (_PACKAGE_KEYS, dataset.packages),
            (_PACKAGE_ITEM_KEYS, dataset.package_items),
            (_RECORD_HANDLE_KEYS, dataset.record_handles),
            (_RECORD_ACTIVITY_KEYS, dataset.record_activity),
            (_RECORD_MENTION_KEYS, dataset.record_mentions),
        ]:
            for alias in key_set:
                if alias in lower_keys:
                    sink.extend(_ensure_list_of_dicts(node[lower_keys[alias]]))

        for alias in _RECORD_KEYS:
            if alias in lower_keys:
                value = node[lower_keys[alias]]
                dataset.records = _normalise_records(value)

        for value in node.values():
            _walk_json_payload(value, dataset)

    elif isinstance(node, list):
        for entry in node:
            _walk_json_payload(entry, dataset)


def _ensure_list_of_dicts(value: Any) -> List[Dict[str, Any]]:
    if value in (None, ""):
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    result: List[Dict[str, Any]] = []
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, Mapping):
                result.append(dict(entry))
    return result


def _normalise_records(value: Any) -> Dict[str, List[Dict[str, Any]]]:
    if isinstance(value, Mapping):
        return {str(key): _ensure_list_of_dicts(payload) for key, payload in value.items()}
    if isinstance(value, list):
        bucket: Dict[str, List[Dict[str, Any]]] = {}
        for entry in value:
            if not isinstance(entry, Mapping):
                continue
            entity_type = str(entry.get("entity_type") or entry.get("type") or "record")
            bucket.setdefault(entity_type, []).append(dict(entry))
        return bucket
    return {}


# ---------------------------------------------------------------------------
# Materialisation helpers
# ---------------------------------------------------------------------------


def _materialise_dataset(dataset: LegacyDataset, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)

    _write_settings(dataset, destination)
    _write_database(dataset, destination / "orders_manager.db")
    _write_records(dataset, destination)
    _write_attachments(dataset, destination / "legacy_assets")
    _write_report(dataset, destination / "legacy_import_report.json")


def _write_settings(dataset: LegacyDataset, destination: Path) -> None:
    payload = dict(dataset.settings)
    payload.setdefault("timezone", dataset.timezone or "UTC")
    (destination / "settings.json").write_text(json.dumps(payload, indent=2, sort_keys=True))


def _write_database(dataset: LegacyDataset, target: Path) -> None:
    if dataset.database_blob:
        target.write_bytes(dataset.database_blob)
        return

    connection = sqlite3.connect(target)
    try:
        _initialise_database_schema(connection)
        _populate_database(connection, dataset)
        connection.commit()
    finally:
        connection.close()


def _initialise_database_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            contact_id TEXT,
            status TEXT,
            total_cents INTEGER,
            title TEXT,
            created_at TEXT,
            updated_at TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_line_items (
            line_item_id TEXT PRIMARY KEY,
            order_id TEXT,
            item_id TEXT,
            quantity INTEGER,
            price_cents INTEGER,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_logs (
            log_id TEXT PRIMARY KEY,
            order_id TEXT,
            message TEXT,
            created_at TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_status_history (
            entry_id TEXT PRIMARY KEY,
            order_id TEXT,
            status TEXT,
            created_at TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id TEXT PRIMARY KEY,
            company_name TEXT,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            price_cents INTEGER,
            weight_oz REAL,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS packages (
            package_id TEXT PRIMARY KEY,
            name TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS package_items (
            package_id TEXT,
            item_id TEXT,
            quantity INTEGER,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (package_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS record_mentions (
            mention_id TEXT PRIMARY KEY,
            entity_type TEXT,
            entity_id TEXT,
            mentioned_handle TEXT,
            created_at TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS record_activity_logs (
            activity_id TEXT PRIMARY KEY,
            entity_type TEXT,
            entity_id TEXT,
            action TEXT,
            created_at TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS record_handles (
            handle TEXT PRIMARY KEY,
            entity_type TEXT,
            entity_id TEXT,
            display_name TEXT,
            search_blob TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )


def _populate_database(conn: sqlite3.Connection, dataset: LegacyDataset) -> None:
    cursor = conn.cursor()

    for order in dataset.orders:
        order_id = _coerce_identifier(order, "order_id", "id", "orderId", "uuid")
        contact_id = _first_value(order, "contact_id", "contactId", "vendor_id", "customer_id")
        total_cents = _extract_money(order, "total_cents", "total", "total_amount")
        cursor.execute(
            """
            INSERT OR REPLACE INTO orders (order_id, contact_id, status, total_cents, title, created_at, updated_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                _normalise_optional(contact_id),
                _normalise_optional(_first_value(order, "status", "state")),
                total_cents,
                _normalise_optional(_first_value(order, "title", "name", "display_id")),
                _normalise_optional(_first_value(order, "created_at", "createdAt", "created")),
                _normalise_optional(_first_value(order, "updated_at", "updatedAt", "modified")),
                json.dumps(order, sort_keys=True),
            ),
        )

    for line_item in dataset.order_line_items:
        line_id = _coerce_identifier(line_item, "line_item_id", "id", "lineId", "uuid")
        cursor.execute(
            """
            INSERT OR REPLACE INTO order_line_items (line_item_id, order_id, item_id, quantity, price_cents, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                line_id,
                _normalise_optional(_first_value(line_item, "order_id", "orderId")),
                _normalise_optional(_first_value(line_item, "item_id", "itemId", "sku")),
                _coerce_int(_first_value(line_item, "quantity", "qty")),
                _extract_money(line_item, "price_cents", "price", "unit_price"),
                json.dumps(line_item, sort_keys=True),
            ),
        )

    for log in dataset.order_logs:
        log_id = _coerce_identifier(log, "log_id", "id", "uuid")
        cursor.execute(
            """
            INSERT OR REPLACE INTO order_logs (log_id, order_id, message, created_at, raw_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                log_id,
                _normalise_optional(_first_value(log, "order_id", "orderId")),
                _normalise_optional(
                    _first_value(log, "message", "note", "description")
                ),
                _normalise_optional(_first_value(log, "created_at", "createdAt")),
                json.dumps(log, sort_keys=True),
            ),
        )

    for history in dataset.order_status_history:
        history_id = _coerce_identifier(history, "id", "entry_id", "uuid")
        cursor.execute(
            """
            INSERT OR REPLACE INTO order_status_history (entry_id, order_id, status, created_at, raw_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                history_id,
                _normalise_optional(_first_value(history, "order_id", "orderId")),
                _normalise_optional(_first_value(history, "status", "state")),
                _normalise_optional(_first_value(history, "created_at", "createdAt")),
                json.dumps(history, sort_keys=True),
            ),
        )

    for contact in dataset.contacts:
        contact_id = _coerce_identifier(contact, "id", "contact_id", "vendor_id", "uuid")
        company_name = _first_value(
            contact,
            "company_name",
            "companyName",
            "name",
            "contact_name",
            "contactName",
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO contacts (id, company_name, contact_name, email, phone, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                contact_id,
                _normalise_optional(company_name),
                _normalise_optional(_first_value(contact, "contact_name", "contactName", "name")),
                _normalise_optional(contact.get("email")),
                _normalise_optional(contact.get("phone")),
                json.dumps(contact, sort_keys=True),
            ),
        )

    for item in dataset.items:
        item_id = _coerce_identifier(item, "id", "item_id", "itemId", "sku")
        cursor.execute(
            """
            INSERT OR REPLACE INTO items (id, name, description, price_cents, weight_oz, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                _normalise_optional(_first_value(item, "name", "title", "label")),
                _normalise_optional(item.get("description")),
                _extract_money(item, "price_cents", "price", "unit_price"),
                _coerce_float(item.get("weight_oz") or item.get("weight")),
                json.dumps(item, sort_keys=True),
            ),
        )

    for package in dataset.packages:
        package_id = _coerce_identifier(package, "package_id", "id", "packageId")
        cursor.execute(
            """
            INSERT OR REPLACE INTO packages (package_id, name, raw_json)
            VALUES (?, ?, ?)
            """,
            (
                package_id,
                _normalise_optional(_first_value(package, "name", "label")),
                json.dumps(package, sort_keys=True),
            ),
        )

    for package_item in dataset.package_items:
        package_id = _normalise_optional(_first_value(package_item, "package_id", "packageId"))
        item_id = _normalise_optional(_first_value(package_item, "item_id", "itemId", "sku"))
        if not package_id or not item_id:
            continue
        cursor.execute(
            """
            INSERT OR REPLACE INTO package_items (package_id, item_id, quantity, raw_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                package_id,
                item_id,
                _coerce_int(_first_value(package_item, "quantity", "qty")),
                json.dumps(package_item, sort_keys=True),
            ),
        )

    for mention in dataset.record_mentions:
        mention_id = _coerce_identifier(mention, "id", "mention_id", "uuid")
        cursor.execute(
            """
            INSERT OR REPLACE INTO record_mentions (mention_id, entity_type, entity_id, mentioned_handle, created_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mention_id,
                _normalise_optional(_first_value(mention, "entity_type", "type")),
                _normalise_optional(_first_value(mention, "entity_id", "entityId")),
                _normalise_optional(_first_value(mention, "handle", "mentioned_handle")),
                _normalise_optional(_first_value(mention, "created_at", "createdAt")),
                json.dumps(mention, sort_keys=True),
            ),
        )

    for activity in dataset.record_activity:
        activity_id = _coerce_identifier(activity, "id", "activity_id", "uuid")
        cursor.execute(
            """
            INSERT OR REPLACE INTO record_activity_logs (activity_id, entity_type, entity_id, action, created_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                _normalise_optional(_first_value(activity, "entity_type", "type")),
                _normalise_optional(_first_value(activity, "entity_id", "entityId")),
                _normalise_optional(_first_value(activity, "action", "verb", "event")),
                _normalise_optional(_first_value(activity, "created_at", "createdAt")),
                json.dumps(activity, sort_keys=True),
            ),
        )

    for handle in dataset.record_handles:
        handle_id = _coerce_identifier(handle, "handle", "id")
        cursor.execute(
            """
            INSERT OR REPLACE INTO record_handles (handle, entity_type, entity_id, display_name, search_blob, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                handle_id,
                _normalise_optional(_first_value(handle, "entity_type", "type")),
                _normalise_optional(_first_value(handle, "entity_id", "entityId")),
                _normalise_optional(_first_value(handle, "display_name", "label", "name")),
                _normalise_optional(_first_value(handle, "search_blob", "search", "terms")),
                json.dumps(handle, sort_keys=True),
            ),
        )

    for entity_type, records in dataset.records.items():
        for record in records:
            entity_id = _coerce_identifier(record, "id", "entity_id", "record_id", "uuid")
            cursor.execute(
                """
                INSERT OR REPLACE INTO records (entity_type, entity_id, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entity_type,
                    entity_id,
                    json.dumps(record, sort_keys=True),
                    _normalise_optional(_first_value(record, "created_at", "createdAt")),
                    _normalise_optional(_first_value(record, "updated_at", "updatedAt")),
                ),
            )


def _write_records(dataset: LegacyDataset, destination: Path) -> None:
    if not dataset.records:
        return
    records_dir = destination / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    for entity_type, entries in dataset.records.items():
        (records_dir / f"{entity_type}.json").write_text(
            json.dumps(entries, indent=2, sort_keys=True)
        )


def _write_attachments(dataset: LegacyDataset, destination: Path) -> None:
    if not dataset.attachments:
        return
    destination.mkdir(parents=True, exist_ok=True)
    for relative_name, payload in dataset.attachments.items():
        safe_path = _safe_attachment_path(relative_name)
        target = destination / safe_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _write_report(dataset: LegacyDataset, target: Path) -> None:
    report = {
        "notes": dataset.notes,
        "summary": {
            "orders": len(dataset.orders),
            "order_line_items": len(dataset.order_line_items),
            "contacts": len(dataset.contacts),
            "items": len(dataset.items),
            "packages": len(dataset.packages),
            "records": sum(len(entries) for entries in dataset.records.values()),
            "attachments": len(dataset.attachments),
            "has_database": dataset.database_blob is not None,
        },
    }
    target.write_text(json.dumps(report, indent=2, sort_keys=True))


def _write_archive(source_dir: Path, destination: Path) -> None:
    with ZipFile(destination, "w") as archive:
        for entry in sorted(source_dir.rglob("*")):
            if entry.is_dir():
                continue
            archive.write(entry, entry.relative_to(source_dir).as_posix())


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _coerce_identifier(payload: MutableMapping[str, Any], *candidates: str) -> str:
    for candidate in candidates:
        value = payload.get(candidate)
        if value not in (None, ""):
            return str(value)
    identifier = uuid.uuid4().hex
    payload.setdefault(candidates[0], identifier)
    return identifier


def _first_value(payload: Mapping[str, Any], *candidates: str) -> Optional[Any]:
    for candidate in candidates:
        if candidate in payload:
            value = payload[candidate]
            if value not in (None, ""):
                return value
    return None


def _extract_money(payload: Mapping[str, Any], *candidates: str) -> int:
    value = _first_value(payload, *candidates)
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value) * (100 if isinstance(value, float) else 1)))
    try:
        cleaned = str(value).strip().replace("$", "")
        if "." in cleaned:
            return int(round(float(cleaned) * 100))
        return int(cleaned)
    except (TypeError, ValueError):
        return 0


def _coerce_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _coerce_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_optional(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _safe_attachment_path(name: str) -> Path:
    parts = []
    for part in Path(name).parts:
        if part in ("", "."):
            continue
        if part == "..":
            parts.append("parent")
        else:
            parts.append(part)
    if not parts:
        parts = [uuid.uuid4().hex]
    return Path(*parts)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

