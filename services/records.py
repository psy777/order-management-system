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
                "due_at": FieldDefinition("due_at", field_type="string"),
                "due_has_time": FieldDefinition("due_has_time", field_type="boolean", default=False),
                "timezone": FieldDefinition("timezone", field_type="string", default="UTC"),
                "completed": FieldDefinition("completed", field_type="boolean", default=False),
                "completed_at": FieldDefinition("completed_at", field_type="string"),
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
        metadata = self._build_handle_metadata(schema, payload)
        self.register_handle(
            conn,
            schema.entity_type,
            payload["id"],
            handle_value,
            display_name,
            search_blob,
            metadata=metadata,
        )

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

    def _build_handle_metadata(
        self,
        schema: RecordSchema,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        builder_name = f"_build_{schema.entity_type}_handle_metadata"
        builder = getattr(self, builder_name, None)
        if callable(builder):
            try:
                metadata = builder(payload)
            except Exception:  # pragma: no cover - defensive guard
                metadata = None
            if metadata:
                return metadata
        return None

    @staticmethod
    def _clean_handle_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not metadata:
            return None
        cleaned: Dict[str, Any] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, str):
                trimmed = value.strip()
                if not trimmed:
                    continue
                cleaned[key] = trimmed
            else:
                cleaned[key] = value
        return cleaned or None

    @staticmethod
    def _build_notes_preview(text: Optional[str], *, limit: int = 160) -> Optional[str]:
        if not text:
            return None
        snippet = str(text).strip()
        if not snippet:
            return None
        if len(snippet) > limit:
            snippet = snippet[: limit - 1].rstrip() + "…"
        return snippet

    def _build_calendar_event_handle_metadata(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        metadata: Dict[str, Any] = {
            "startAt": payload.get("start_at"),
            "endAt": payload.get("end_at"),
            "allDay": bool(payload.get("all_day")),
            "timezone": payload.get("timezone"),
            "location": payload.get("location"),
        }
        notes_preview = self._build_notes_preview(payload.get("notes"))
        if notes_preview:
            metadata["notesPreview"] = notes_preview
        return self._clean_handle_metadata(metadata)

    def _build_reminder_handle_metadata(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        metadata: Dict[str, Any] = {
            "dueAt": payload.get("due_at"),
            "dueHasTime": bool(payload.get("due_has_time")),
            "timezone": payload.get("timezone"),
            "completed": bool(payload.get("completed")),
            "completedAt": payload.get("completed_at"),
        }
        notes_preview = self._build_notes_preview(payload.get("notes"))
        if notes_preview:
            metadata["notesPreview"] = notes_preview
        return self._clean_handle_metadata(metadata)

    def register_handle(
        self,
        conn: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        handle: str,
        display_name: Optional[str] = None,
        search_blob: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not handle:
            return
        normalised_handle = handle.lower()
        display_value = (display_name or handle).strip()
        search_value = (search_blob or display_value).lower()
        metadata_value = self._clean_handle_metadata(metadata)
        conn.execute(
            "DELETE FROM record_handles WHERE entity_type = ? AND entity_id = ?",
            (entity_type, str(entity_id)),
        )
        conn.execute(
            """
            INSERT INTO record_handles (handle, entity_type, entity_id, display_name, search_blob, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                entity_type=excluded.entity_type,
                entity_id=excluded.entity_id,
                display_name=excluded.display_name,
                search_blob=excluded.search_blob,
                metadata_json=excluded.metadata_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                normalised_handle,
                entity_type,
                str(entity_id),
                display_value,
                search_value,
                json.dumps(metadata_value) if metadata_value is not None else None,
            ),
        )

    def list_handles(
        self,
        conn: sqlite3.Connection,
        entity_types: Optional[Sequence[str]] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT handle, entity_type, entity_id, display_name, metadata_json FROM record_handles"
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
        results = []
        for row in cursor.fetchall():
            metadata_payload = row["metadata_json"]
            metadata_value = None
            if metadata_payload:
                try:
                    metadata_value = json.loads(metadata_payload)
                except (TypeError, json.JSONDecodeError):  # pragma: no cover - defensive
                    metadata_value = None
            results.append(
                {
                    "handle": row["handle"],
                    "entityType": row["entity_type"],
                    "entityId": row["entity_id"],
                    "displayName": row["display_name"],
                    "metadata": metadata_value,
                }
            )
        return results

    def resolve_handles(self, conn: sqlite3.Connection, handles: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        if not handles:
            return {}
        placeholders = ",".join(["?"] * len(handles))
        cursor = conn.execute(
            f"SELECT handle, entity_type, entity_id, display_name, metadata_json FROM record_handles WHERE handle IN ({placeholders})",
            [handle.lower() for handle in handles],
        )
        mapping: Dict[str, Dict[str, Any]] = {}
        for row in cursor.fetchall():
            metadata_payload = row["metadata_json"]
            metadata_value = None
            if metadata_payload:
                try:
                    metadata_value = json.loads(metadata_payload)
                except (TypeError, json.JSONDecodeError):  # pragma: no cover - defensive
                    metadata_value = None
            mapping[row["handle"]] = {
                "handle": row["handle"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "display_name": row["display_name"],
                "metadata": metadata_value,
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


__all__ = [
    "FieldDefinition",
    "RecordRegistry",
    "RecordSchema",
    "RecordService",
    "RecordValidationError",
    "bootstrap_record_service",
    "extract_mentions",
    "get_record_service",
    "sync_record_mentions",
]
