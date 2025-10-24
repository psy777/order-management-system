# Order Management System

## Data storage

Application data (SQLite database, attachments, and configuration files) now live in a single directory at `order-management-system/data`. The folder is created automatically on startup and any legacy files that previously lived in the parent `/data` directory are migrated into the new location.

Backups produced by `/api/export-data` capture the entire contents of this directory, so archives include both uploaded files and the `orders_manager.db` database. Restores via `/api/import-data` expect a zip created from the same directory structure.

Uploaded files served from `/data/<filename>` are stored directly inside this shared directory. If you manage the application manually, you only need to back up the `data/` folder to retain all persistent state.

## Upgrading to the latest release

The repository ships with an `upgrade.py` helper that safely fast-forwards the
codebase to the latest commit on the `master` branch while preserving user
data. The script first verifies that your working tree is clean, creates a ZIP
backup of the `data/` directory, pulls the newest code, and finally reinstalls
Python dependencies.

## Windows quick launch

For a one-click start on Windows, use the `launch_firecoast.bat` script in the
project root. The batch file creates (or reuses) a local virtual environment,
installs dependencies from `requirements.txt`, and then runs `app.py`. You can
create a desktop or taskbar shortcut that points directly to the batch file to
start the application without opening a terminal first.

1. Right-click `launch_firecoast.bat` and choose **Create shortcut**.
2. Move the shortcut to your desktop or pin it to the taskbar/start menu.
3. Double-click the shortcut whenever you want to launch FireCoast.

The first launch may take a moment while Python dependencies install. Later
launches reuse the existing virtual environment and start immediately.

To upgrade an installation:

1. Ensure your working tree is clean (`git status` should report no pending
   changes). The script refuses to run if it detects uncommitted work so you
   have a chance to stash or commit first.
2. From the project root, execute:

   ```
   python upgrade.py
   ```

   The helper will create a ZIP backup of `data/`, fetch the latest
   `master` commit from `origin`, hard-reset your local `master` to that
   revision, and reinstall dependencies from `requirements.txt`.

You can customise the source remote or branch, or skip dependency installation
if you prefer to manage it manually:

```
python upgrade.py --remote upstream --branch master --skip-deps
```

On success the command prints where the backup archive lives (under
`upgrade_backups/`) and the previous/current Git revisions. If anything goes
wrong you can restore the backup through the `/api/import-data` endpoint or the
`services.backup` helpers.

Installations deployed without Git metadata (for example from a ZIP archive)
will still upgrade successfully: the helper clones the
`https://github.com/psy777/FireCoast.git` repository, replaces the application
files while preserving your `data/` directory, and records the new revision in
`.firecoast_revision`. Set the `FIRECOAST_UPGRADE_REPO` environment variable or
provide a `repositoryUrl` in the API request to override the source repository.

## Schema-driven records

The backend now exposes a reusable record framework in `services/records.py`. A `RecordSchema` describes the fields for each entity type, including which attributes support @mentions. Schemas are persisted in the `record_schemas` table and can be registered at runtime through the new API:

```
POST /api/records/schemas
Content-Type: application/json

{
  "entity_type": "note",
  "description": "Lightweight Notes app",
  "fields": [
    {"name": "title", "field_type": "string", "required": true},
    {"name": "body", "field_type": "text", "required": true, "mention": true},
    {"name": "handle", "field_type": "string", "required": true}
  ],
  "handle_field": "handle",
  "display_field": "title"
}
```

Once a schema exists you can create, update, and inspect records with the following endpoints:

* `GET /api/records/<entity_type>` – list all records for the schema.
* `POST /api/records/<entity_type>` – create a record; request bodies may include an optional `actor` to populate the activity log.
* `GET /api/records/<entity_type>/<entity_id>` – fetch a single record.
* `PUT /api/records/<entity_type>/<entity_id>` – update a record.
* `GET /api/records/<entity_type>/<entity_id>/activity` – retrieve the structured activity log.

All records automatically gain @mention extraction. Any field marked with `"mention": true` will parse handles with the shared regex and store results in `record_mentions`. The `/api/records/handles` endpoint provides a unified directory across contacts, notes, and future entity types for autocomplete suggestions.

### Notes application example

The default install ships with a `note` schema to illustrate how a downstream app can participate. Creating a new note automatically registers its handle, logs activity, and records mentions:

```
POST /api/records/note
{
  "title": "CSAT follow-up",
  "body": "Coordinate with @clientalpha and link back to @note-alpha.",
  "handle": "note-alpha",
  "actor": "support"
}
```

Mentioned contacts (resolved via their handles) appear in `record_mentions`, allowing order timelines and profile pages to surface cross-entity references without bespoke SQL.

## Front-end mention components

React-powered islands now rely on a shared library located at `assets/js/record_mentions.jsx`. The module exports a `RecordMentionTextarea` that queries `/api/records/handles` and renders consistent pills, autocomplete, and keyboard navigation. To migrate existing components replace bespoke textarea logic with:

```jsx
const { RecordMentionTextarea } = window.RecordMentionComponents;

<RecordMentionTextarea
  value={formState.notes}
  onChange={value => setFormState(prev => ({ ...prev, notes: value }))}
  entityTypes={['contact', 'note']}
  rows={4}
  placeholder="Mention teammates and linked notes with @handle"
/>
```

The reusable component supports multiple entity domains through the `entityTypes` prop, so applications can opt into @mentions across contacts, notes, and any additional schema registered in the backend.
