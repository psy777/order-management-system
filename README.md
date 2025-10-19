# Order Management System

## Data storage

Application data (SQLite database, attachments, and configuration files) now live in a single directory at `order-management-system/data`. The folder is created automatically on startup and any legacy files that previously lived in the parent `/data` directory are migrated into the new location.

Backups produced by `/api/export-data` capture the entire contents of this directory, so archives include both uploaded files and the `orders_manager.db` database. Restores via `/api/import-data` expect a zip created from the same directory structure.

Uploaded files served from `/data/<filename>` are stored directly inside this shared directory. If you manage the application manually, you only need to back up the `data/` folder to retain all persistent state.
