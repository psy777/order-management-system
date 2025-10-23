"""Schema-driven record management utilities for the OMS application."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

MENTION_PATTERN = re.compile(r"(?<!\S)@([A-Za-z0-9_.-]+)")


class RecordValidationError(Exception):
    """Raised when record validation fails."""

    def __init__(self, errors: Dict[str, str]):
        super().__init__("Record validation failed")
        self.errors = errors


@dataclass
class FieldDefinition:
    """Represents a single field inside a record schema."""

    name: str
    field_type: str = "string"
    required: bool = False
    default: Any = None
    mention: bool = False
    description: str = ""
    choices: Optional[Sequence[Any]] = None

    def clean(self, value: Any) -> Any:
        """Normalise input data for this field."""
        if value is None:
            return None
        if self.field_type in {"string", "text"}:
            if value is None:
                return ""
            return str(value)
        if self.field_type == "integer":
            if value == "":
                return None
            return int(value)
        if self.field_type == "number":
            if value == "":
                return None
            return float(value)
        if self.field_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                return value.strip().lower() in {"true", "1", "yes", "y"}
            return bool(value)
        if self.field_type == "json":
            if isinstance(value, (dict, list)):
                return value
            if isinstance(value, str) and value.strip():
                return json.loads(value)
            return {}
        return value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "field_type": self.field_type,
            "required": self.required,
            "default": self.default,
            "mention": self.mention,
            "description": self.description,
            "choices": list(self.choices) if self.choices is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FieldDefinition":
        return cls(
            name=payload["name"],
            field_type=payload.get("field_type", "string"),
            required=payload.get("required", False),
            default=payload.get("default"),
            mention=payload.get("mention", False),
            description=payload.get("description", ""),
            choices=payload.get("choices"),
        )


@dataclass
class RecordSchema:
    """Describes a polymorphic record type."""

    entity_type: str
    fields: Dict[str, FieldDefinition]
    handle_field: Optional[str] = None
    display_field: Optional[str] = None
    mention_fields: List[str] = field(default_factory=list)
    description: str = ""
    storage: str = "records"
    metadata: Dict[str, Any] = field(default_factory=dict)
    persist: bool = True

    def validate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        errors: Dict[str, str] = {}
        normalised: Dict[str, Any] = {}
        for name, definition in self.fields.items():
            incoming = payload.get(name, None)
            if incoming in (None, ""):
                if definition.required and definition.default is None:
                    errors[name] = "Field is required"
                    continue
                if definition.default is not None:
                    default_value = definition.default() if callable(definition.default) else definition.default
                    try:
                        normalised[name] = definition.clean(default_value)
                    except (ValueError, TypeError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
                        errors[name] = str(exc)
                continue
            try:
                normalised[name] = definition.clean(incoming)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                errors[name] = str(exc)
        if errors:
            raise RecordValidationError(errors)
        for name, value in payload.items():
            if name not in normalised and name in self.fields and value not in (None, ""):
                try:
                    normalised[name] = self.fields[name].clean(value)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    errors[name] = str(exc)
        if errors:
            raise RecordValidationError(errors)
        return normalised

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "fields": [field.to_dict() for field in self.fields.values()],
            "handle_field": self.handle_field,
            "display_field": self.display_field,
            "mention_fields": self.mention_fields,
            "description": self.description,
            "storage": self.storage,
            "metadata": self.metadata,
            "persist": self.persist,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RecordSchema":
        fields_payload = payload.get("fields", [])
        field_map = {definition["name"]: FieldDefinition.from_dict(definition) for definition in fields_payload}
        mention_fields = payload.get("mention_fields")
        if not mention_fields:
            mention_fields = [name for name, field in field_map.items() if field.mention]
        return cls(
            entity_type=payload["entity_type"],
            fields=field_map,
            handle_field=payload.get("handle_field"),
            display_field=payload.get("display_field"),
            mention_fields=mention_fields or [],
            description=payload.get("description", ""),
            storage=payload.get("storage", "records"),
            metadata=payload.get("metadata", {}),
            persist=payload.get("persist", True),
        )

    def resolve_display_value(self, data: Dict[str, Any]) -> str:
        if self.display_field and data.get(self.display_field):
            return str(data[self.display_field])
        if self.handle_field and data.get(self.handle_field):
            return str(data[self.handle_field])
        for candidate in ("title", "name", "contactName", "companyName"):
            if data.get(candidate):
                return str(data[candidate])
        return self.entity_type.title()

    def build_search_blob(self, data: Dict[str, Any]) -> str:
        pieces: List[str] = []
        for field_name, definition in self.fields.items():
            value = data.get(field_name)
            if isinstance(value, str):
                pieces.append(value)
        if self.handle_field and data.get(self.handle_field):
            pieces.append(str(data[self.handle_field]))
        return " ".join(piece.strip() for piece in pieces if piece).lower()

    def iter_mention_fields(self) -> Iterable[str]:
        if self.mention_fields:
            return list(self.mention_fields)
        return [name for name, field in self.fields.items() if field.mention]


class RecordRegistry:
    """In-memory registry of schemas."""

    def __init__(self) -> None:
        self._schemas: Dict[str, RecordSchema] = {}

    def register(self, schema: RecordSchema) -> None:
        self._schemas[schema.entity_type] = schema

    def clear(self) -> None:
        """Remove all registered schemas."""
        self._schemas.clear()

    def get(self, entity_type: str) -> RecordSchema:
        if entity_type not in self._schemas:
            raise KeyError(f"Unknown record type '{entity_type}'")
        return self._schemas[entity_type]

    def has(self, entity_type: str) -> bool:
        return entity_type in self._schemas

    def all(self) -> List[RecordSchema]:
        return list(self._schemas.values())


class RecordService:
    """High level facade that coordinates schema registration and record persistence."""

    def __init__(self, registry: RecordRegistry):
        self.registry = registry

    # ------------------------------------------------------------------
    # Bootstrapping & schema management
    # ------------------------------------------------------------------
    def bootstrap(self, conn: sqlite3.Connection) -> None:
        """Load schemas from storage and register built-in definitions."""
        self._load_registered_schemas(conn)
        self._register_builtin_contact_schema()
        self._ensure_default_note_schema(conn)
        self._ensure_calendar_event_schema(conn)
        self._ensure_reminder_schema(conn)

    def _load_registered_schemas(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("SELECT entity_type, schema_json FROM record_schemas")
        for row in cursor.fetchall():
            try:
                schema_payload = json.loads(row["schema_json"])
            except json.JSONDecodeError:
                continue
            schema = RecordSchema.from_dict(schema_payload)
            self.registry.register(schema)

    def _register_builtin_contact_schema(self) -> None:
        if self.registry.has("contact"):
            return
        contact_schema = RecordSchema(
            entity_type="contact",
            fields={
                "id": FieldDefinition("id", field_type="string", required=True),
                "contactName": FieldDefinition("contactName", field_type="string"),
                "companyName": FieldDefinition("companyName", field_type="string"),
                "email": FieldDefinition("email", field_type="string"),
                "phone": FieldDefinition("phone", field_type="string"),
                "handle": FieldDefinition("handle", field_type="string", required=True),
                "notes": FieldDefinition("notes", field_type="text", mention=True),
            },
            handle_field="handle",
            display_field="contactName",
            description="Core CRM contacts stored in the legacy contacts table.",
            storage="external",
            metadata={"list_endpoint": "/api/contacts"},
            persist=False,
        )
        self.registry.register(contact_schema)

    def _ensure_default_note_schema(self, conn: sqlite3.Connection) -> None:
        if self.registry.has("note"):
            return
        note_schema = RecordSchema(
            entity_type="note",
            fields={
                "title": FieldDefinition("title", field_type="string", required=True),
                "body": FieldDefinition("body", field_type="text", required=True, mention=True),
                "handle": FieldDefinition("handle", field_type="string", required=True),
                "author": FieldDefinition("author", field_type="string"),
            },
            handle_field="handle",
            display_field="title",
            description="General purpose notes that support @mentions out of the box.",
            storage="records",
            metadata={"example": True},
        )
        self.register_schema(conn, note_schema)

    def _ensure_calendar_event_schema(self, conn: sqlite3.Connection) -> None:
        if self.registry.has("calendar_event"):
            return
        calendar_schema = RecordSchema(
            entity_type="calendar_event",
            fields={
                "title": FieldDefinition("title", field_type="string", required=True),
                "handle": FieldDefinition("handle", field_type="string", required=True),
                "start_at": FieldDefinition("start_at", field_type="string", required=True),
                "end_at": FieldDefinition("end_at", field_type="string"),
                "all_day": FieldDefinition("all_day", field_type="boolean", default=False),
                "location": FieldDefinition("location", field_type="string"),
                "notes": FieldDefinition("notes", field_type="text", mention=True),
                "timezone": FieldDefinition("timezone", field_type="string", default="UTC"),
            },
            handle_field="handle",
            display_field="title",
            description="Calendar events with scheduling metadata and mention-enabled notes.",
            storage="records",
        )
        self.register_schema(conn, calendar_schema)

    def _ensure_reminder_schema(self, conn: sqlite3.Connection) -> None:
        if self.registry.has("reminder"):
            return
        reminder_schema = RecordSchema(
            entity_type="reminder",
            fields={
                "title": FieldDefinition("title", field_type="string", required=True),
                "handle": FieldDefinition("handle", field_type="string", required=True),
                "notes": FieldDefinition("notes", field_type="text", mention=True),
                "kind": FieldDefinition("kind", field_type="string", default="reminder"),
                "due_at": FieldDefinition("due_at", field_type="string"),
                "due_has_time": FieldDefinition("due_has_time", field_type="boolean", default=False),
                "remind_at": FieldDefinition("remind_at", field_type="string"),
                "timer_seconds": FieldDefinition("timer_seconds", field_type="integer"),
                "timezone": FieldDefinition("timezone", field_type="string", default="UTC"),
                "completed": FieldDefinition("completed", field_type="boolean", default=False),
                "completed_at": FieldDefinition("completed_at", field_type="string"),
                "persistent": FieldDefinition("persistent", field_type="boolean", default=False),
                "last_notified_at": FieldDefinition("last_notified_at", field_type="string"),
                "context_note_id": FieldDefinition("context_note_id", field_type="string"),
            },
            handle_field="handle",
            display_field="title",
            description="Operational reminders with optional due dates and mention-enabled notes.",
            storage="records",
        )
        self.register_schema(conn, reminder_schema)

    def register_schema(self, conn: sqlite3.Connection, schema_payload: Any) -> RecordSchema:
        schema = schema_payload if isinstance(schema_payload, RecordSchema) else RecordSchema.from_dict(schema_payload)
        self.registry.register(schema)
        if schema.persist:
            conn.execute(
                """
                INSERT INTO record_schemas (entity_type, schema_json, description, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(entity_type) DO UPDATE SET
                    schema_json=excluded.schema_json,
                    description=excluded.description,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (schema.entity_type, json.dumps(schema.to_dict()), schema.description),
            )
        return schema

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------
    def create_record(
        self,
        conn: sqlite3.Connection,
        entity_type: str,
        payload: Dict[str, Any],
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        schema = self.registry.get(entity_type)
        if schema.storage != "records":
            raise ValueError(f"Record type '{entity_type}' is externally managed and cannot be created via API")
        normalised = schema.validate(payload)
        entity_id = str(payload.get("id") or uuid.uuid4())
        normalised["id"] = entity_id
        conn.execute(
            """
            INSERT INTO records (entity_type, entity_id, data)
            VALUES (?, ?, ?)
            """,
            (entity_type, entity_id, json.dumps(normalised)),
        )
        self._register_handle_if_applicable(conn, schema, normalised)
        self.log_activity(conn, entity_type, entity_id, "created", actor, normalised)
        self._sync_mentions_for_record(conn, schema, normalised)
        return {"entityType": entity_type, "id": entity_id, "data": normalised}

    def update_record(
        self,
        conn: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        payload: Dict[str, Any],
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        schema = self.registry.get(entity_type)
        if schema.storage != "records":
            raise ValueError(f"Record type '{entity_type}' is externally managed and cannot be mutated via API")
        cursor = conn.execute(
            "SELECT data FROM records WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"Record {entity_type}:{entity_id} not found")
        current_data = json.loads(row["data"])
        current_data.update(payload)
        normalised = schema.validate(current_data)
        normalised["id"] = entity_id
        conn.execute(
            """
            UPDATE records
            SET data = ?, updated_at = CURRENT_TIMESTAMP
            WHERE entity_type = ? AND entity_id = ?
            """,
            (json.dumps(normalised), entity_type, entity_id),
        )
        self._register_handle_if_applicable(conn, schema, normalised)
        self.log_activity(conn, entity_type, entity_id, "updated", actor, normalised)
        self._sync_mentions_for_record(conn, schema, normalised)
        return {"entityType": entity_type, "id": entity_id, "data": normalised}

    def get_record(self, conn: sqlite3.Connection, entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
        schema = self.registry.get(entity_type)
        if schema.storage != "records":
            raise ValueError(f"Record type '{entity_type}' is externally managed")
        cursor = conn.execute(
            "SELECT data, created_at, updated_at FROM records WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        payload = json.loads(row["data"])
        payload["created_at"] = row["created_at"]
        payload["updated_at"] = row["updated_at"]
        return payload

    def list_records(self, conn: sqlite3.Connection, entity_type: str) -> List[Dict[str, Any]]:
        schema = self.registry.get(entity_type)
        if schema.storage != "records":
            raise ValueError(f"Record type '{entity_type}' is externally managed")
        cursor = conn.execute(
            "SELECT entity_id, data FROM records WHERE entity_type = ? ORDER BY updated_at DESC",
            (entity_type,),
        )
        results: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            payload = json.loads(row["data"])
            payload["id"] = row["entity_id"]
            results.append(payload)
        return results

    def delete_record(
        self,
        conn: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
    ) -> None:
        schema = self.registry.get(entity_type)
        if schema.storage != "records":
            raise ValueError(f"Record type '{entity_type}' is externally managed and cannot be removed via API")
        cursor = conn.execute(
            "SELECT data FROM records WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"Record {entity_type}:{entity_id} not found")
        conn.execute(
            "DELETE FROM records WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        conn.execute(
            "DELETE FROM record_handles WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        conn.execute(
            "DELETE FROM record_mentions WHERE context_entity_type = ? AND context_entity_id = ?",
            (entity_type, entity_id),
        )
        conn.execute(
            "DELETE FROM record_mentions WHERE mentioned_entity_type = ? AND mentioned_entity_id = ?",
            (entity_type, entity_id),
        )
        conn.execute(
            "DELETE FROM record_activity_logs WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )

    # ------------------------------------------------------------------
    # Mentions & handles
    # ------------------------------------------------------------------
    def _register_handle_if_applicable(
        self,
        conn: sqlite3.Connection,
        schema: RecordSchema,
        payload: Dict[str, Any],
    ) -> None:
        if not schema.handle_field:
            return
        handle_value = payload.get(schema.handle_field)
        if not handle_value:
            return
        display_name = schema.resolve_display_value(payload)
        search_blob = schema.build_search_blob(payload)
        self.register_handle(conn, schema.entity_type, payload["id"], handle_value, display_name, search_blob)

    def _sync_mentions_for_record(
        self,
        conn: sqlite3.Connection,
        schema: RecordSchema,
        payload: Dict[str, Any],
    ) -> None:
        mention_fields = list(schema.iter_mention_fields())
        if not mention_fields:
            return
        handles: List[str] = []
        snippet_source: Optional[str] = None
        for field_name in mention_fields:
            value = payload.get(field_name)
            if isinstance(value, str):
                extracted = extract_mentions(value)
                handles.extend(extracted)
                if not snippet_source and value.strip():
                    snippet_source = value
        unique_handles = sorted({handle.lower() for handle in handles})
        if not unique_handles:
            sync_record_mentions(conn, [], schema.entity_type, str(payload["id"]), snippet_source or "")
            return
        sync_record_mentions(conn, unique_handles, schema.entity_type, str(payload["id"]), snippet_source or "")

    def register_handle(
        self,
        conn: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        handle: str,
        display_name: Optional[str] = None,
        search_blob: Optional[str] = None,
    ) -> None:
        if not handle:
            return
        normalised_handle = handle.lower()
        display_value = (display_name or handle).strip()
        search_value = (search_blob or display_value).lower()
        conn.execute(
            "DELETE FROM record_handles WHERE entity_type = ? AND entity_id = ?",
            (entity_type, str(entity_id)),
        )
        conn.execute(
            """
            INSERT INTO record_handles (handle, entity_type, entity_id, display_name, search_blob)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                entity_type=excluded.entity_type,
                entity_id=excluded.entity_id,
                display_name=excluded.display_name,
                search_blob=excluded.search_blob,
                updated_at=CURRENT_TIMESTAMP
            """,
            (normalised_handle, entity_type, str(entity_id), display_value, search_value),
        )

    def list_handles(
        self,
        conn: sqlite3.Connection,
        entity_types: Optional[Sequence[str]] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT handle, entity_type, entity_id, display_name FROM record_handles"
        conditions: List[str] = []
        params: List[Any] = []
        if entity_types:
            placeholders = ",".join(["?"] * len(entity_types))
            conditions.append(f"entity_type IN ({placeholders})")
            params.extend(entity_types)
        if search:
            conditions.append("search_blob LIKE ?")
            params.append(f"%{search.lower()}%")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY display_name COLLATE NOCASE ASC"
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        results: List[Dict[str, Any]] = []
        contact_ids: List[str] = []

        for row in rows:
            entity_type = row["entity_type"]
            entity_id = row["entity_id"]
            if entity_type and entity_type.lower() == "contact" and entity_id:
                contact_ids.append(str(entity_id))
            results.append(
                {
                    "handle": row["handle"],
                    "entityType": entity_type,
                    "entityId": entity_id,
                    "displayName": row["display_name"],
                }
            )

        if contact_ids:
            placeholders = ",".join(["?"] * len(contact_ids))
            contact_cursor = conn.execute(
                f"""
                SELECT id, company_name, contact_name, email, phone, details_json
                FROM contacts
                WHERE id IN ({placeholders})
                """,
                contact_ids,
            )
            contact_rows = {}
            for contact_row in contact_cursor.fetchall():
                contact_id = str(contact_row["id"])
                contact_name = (contact_row["contact_name"] or "").strip()
                company_name = (contact_row["company_name"] or "").strip()
                fallback_email = (contact_row["email"] or "").strip()
                fallback_phone = (contact_row["phone"] or "").strip()
                raw_details = contact_row["details_json"]
                details_payload: Dict[str, Any] = {}
                if raw_details:
                    try:
                        details_payload = json.loads(raw_details) or {}
                    except json.JSONDecodeError:
                        details_payload = {}

                def _normalise_entries(values: Any, *, allow_blank: bool = False) -> List[Dict[str, Any]]:
                    if not isinstance(values, list):
                        return []
                    normalised: List[Dict[str, Any]] = []
                    for entry in values:
                        if not isinstance(entry, dict):
                            continue
                        value = (entry.get("value") or "").strip()
                        if not value and not allow_blank:
                            continue
                        normalised.append(
                            {
                                "label": (entry.get("label") or "").strip(),
                                "value": value,
                                "isPrimary": bool(entry.get("isPrimary")),
                                "formatted": (entry.get("formatted") or "").strip(),
                            }
                        )
                    return normalised

                email_entries = _normalise_entries(details_payload.get("emails"))
                phone_entries = _normalise_entries(details_payload.get("phones"))

                def _ensure_entry(entries: List[Dict[str, Any]], value: str, *, label: str = "") -> None:
                    cleaned = (value or "").strip()
                    if not cleaned:
                        return
                    for entry in entries:
                        if entry["value"].lower() == cleaned.lower():
                            return
                    entries.append({"label": label.strip(), "value": cleaned, "isPrimary": False, "formatted": cleaned})

                _ensure_entry(email_entries, fallback_email)
                _ensure_entry(phone_entries, fallback_phone)

                def _pick_primary(entries: List[Dict[str, Any]], fallback: str = "") -> Dict[str, Any]:
                    for entry in entries:
                        if entry.get("isPrimary"):
                            return entry
                    if entries:
                        return entries[0]
                    cleaned_fallback = (fallback or "").strip()
                    if cleaned_fallback:
                        return {"label": "", "value": cleaned_fallback, "isPrimary": True, "formatted": cleaned_fallback}
                    return {}

                primary_email_entry = _pick_primary(email_entries, fallback_email)
                primary_phone_entry = _pick_primary(phone_entries, fallback_phone)

                address_entries_raw = details_payload.get("addresses")
                address_entries: List[Dict[str, Any]] = []
                if isinstance(address_entries_raw, list):
                    for entry in address_entries_raw:
                        if not isinstance(entry, dict):
                            continue
                        street = (entry.get("street") or "").strip()
                        city = (entry.get("city") or "").strip()
                        state = (entry.get("state") or "").strip()
                        postal_code = (entry.get("postalCode") or "").strip()
                        if not any([street, city, state, postal_code]):
                            continue
                        city_state = ", ".join(part for part in [city, state] if part)
                        line_two = " ".join(part for part in [city_state, postal_code] if part)
                        lines = [line for line in [street, line_two] if line]
                        address_entries.append(
                            {
                                "label": (entry.get("label") or "").strip() or "Address",
                                "value": "\n".join(lines),
                                "lines": lines,
                                "isPrimary": bool(entry.get("isPrimary")),
                            }
                        )

                contact_rows[contact_id] = {
                    "companyName": company_name,
                    "contactName": contact_name,
                    "email": primary_email_entry.get("value", "").strip(),
                    "emailLabel": (primary_email_entry.get("label") or "").strip(),
                    "emailIsPrimary": bool(primary_email_entry.get("isPrimary")),
                    "emailValue": primary_email_entry.get("value", "").strip(),
                    "phone": primary_phone_entry.get("formatted") or primary_phone_entry.get("value", "").strip(),
                    "phoneLabel": (primary_phone_entry.get("label") or "").strip(),
                    "phoneIsPrimary": bool(primary_phone_entry.get("isPrimary")),
                    "phoneValue": primary_phone_entry.get("value", "").strip(),
                    "emails": email_entries,
                    "phones": phone_entries,
                    "addresses": address_entries,
                }
        else:
            contact_rows = {}

        for entry in results:
            if entry["entityType"] and entry["entityType"].lower() == "contact":
                contact_details = contact_rows.get(str(entry["entityId"]))
                if contact_details:
                    entry["contact"] = contact_details

        return results

    def resolve_handles(self, conn: sqlite3.Connection, handles: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        if not handles:
            return {}
        placeholders = ",".join(["?"] * len(handles))
        cursor = conn.execute(
            f"SELECT handle, entity_type, entity_id, display_name FROM record_handles WHERE handle IN ({placeholders})",
            [handle.lower() for handle in handles],
        )
        mapping: Dict[str, Dict[str, Any]] = {}
        for row in cursor.fetchall():
            mapping[row["handle"]] = {
                "handle": row["handle"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "display_name": row["display_name"],
            }
        return mapping

    # ------------------------------------------------------------------
    # Activity logs
    # ------------------------------------------------------------------
    def log_activity(
        self,
        conn: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO record_activity_logs (entity_type, entity_id, action, actor, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                entity_type,
                str(entity_id),
                action,
                actor,
                json.dumps(payload or {}),
            ),
        )

    def fetch_activity(
        self,
        conn: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        cursor = conn.execute(
            """
            SELECT action, actor, payload, created_at
            FROM record_activity_logs
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (entity_type, str(entity_id), limit),
        )
        activity: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            payload = {}
            if row["payload"]:
                try:
                    payload = json.loads(row["payload"])
                except json.JSONDecodeError:
                    payload = {"raw": row["payload"]}
            activity.append(
                {
                    "action": row["action"],
                    "actor": row["actor"],
                    "payload": payload,
                    "created_at": row["created_at"],
                }
            )
        return activity


# ----------------------------------------------------------------------
# Mention helpers
# ----------------------------------------------------------------------

def extract_mentions(text: Optional[str]) -> List[str]:
    if not text:
        return []
    handles: List[str] = []
    for match in MENTION_PATTERN.finditer(text):
        handle = match.group(1).lower()
        if handle not in handles:
            handles.append(handle)
    return handles


def sync_record_mentions(
    conn: sqlite3.Connection,
    handles: Sequence[str],
    context_entity_type: str,
    context_entity_id: str,
    snippet: Optional[str] = None,
) -> None:
    conn.execute(
        "DELETE FROM record_mentions WHERE context_entity_type = ? AND context_entity_id = ?",
        (context_entity_type, str(context_entity_id)),
    )
    if not handles:
        return
    service = get_record_service()
    resolved = service.resolve_handles(conn, handles)
    snippet_text = (snippet or "").strip()
    if len(snippet_text) > 500:
        snippet_text = snippet_text[:497] + "..."
    for handle in handles:
        metadata = resolved.get(handle.lower())
        if not metadata:
            continue
        conn.execute(
            """
            INSERT INTO record_mentions (
                mentioned_handle,
                mentioned_entity_type,
                mentioned_entity_id,
                context_entity_type,
                context_entity_id,
                snippet
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                metadata["handle"],
                metadata["entity_type"],
                str(metadata["entity_id"]),
                context_entity_type,
                str(context_entity_id),
                snippet_text,
            ),
        )


# ----------------------------------------------------------------------
# Module level singleton used by the Flask app
# ----------------------------------------------------------------------

_registry = RecordRegistry()
_service = RecordService(_registry)


def get_record_service() -> RecordService:
    return _service


def bootstrap_record_service(conn: sqlite3.Connection) -> RecordService:
    _service.bootstrap(conn)
    return _service


def reset_record_service() -> RecordService:
    """Reset cached registry state so it can be reloaded from the database."""
    _registry.clear()
    return _service


__all__ = [
    "FieldDefinition",
    "RecordRegistry",
    "RecordSchema",
    "RecordService",
    "RecordValidationError",
    "bootstrap_record_service",
    "extract_mentions",
    "get_record_service",
    "reset_record_service",
    "sync_record_mentions",
]
