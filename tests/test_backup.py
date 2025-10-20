import io
import json
import zipfile
from pathlib import Path

import pytest

import app as firecoast_app
from services import backup as backup_service


@pytest.fixture(autouse=True)
def set_testing_flag():
    original = firecoast_app.app.config.get('TESTING')
    firecoast_app.app.config['TESTING'] = True
    try:
        yield
    finally:
        if original is None:
            firecoast_app.app.config.pop('TESTING', None)
        else:
            firecoast_app.app.config['TESTING'] = original


@pytest.fixture()
def temp_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()

    monkeypatch.setattr(firecoast_app, 'DATA_DIR', data_dir)
    monkeypatch.setattr(firecoast_app, 'UPLOAD_FOLDER', data_dir)
    firecoast_app.app.config['UPLOAD_FOLDER'] = str(data_dir)

    monkeypatch.setattr(backup_service, 'ensure_data_root', lambda: data_dir)

    return data_dir


@pytest.fixture()
def reset_backup_module(monkeypatch):
    # Ensure helper directories are cleaned up between tests
    yield
    for suffix in ('temp_backups', 'data_temp_backup', 'data_restore_tmp'):
        path = backup_service.ensure_data_root().parent / suffix
        if path.exists():
            if path.is_dir():
                import shutil
                shutil.rmtree(path)
            else:
                path.unlink()


def create_sample_data(data_dir: Path):
    (data_dir / 'orders_manager.db').write_text('db')
    (data_dir / 'settings.json').write_text(json.dumps({'timezone': 'UTC'}))
    uploads = data_dir / 'uploads'
    uploads.mkdir()
    (uploads / 'image.png').write_bytes(b'PNGDATA')


def test_create_backup_archive_includes_all_files(temp_data_dir, reset_backup_module):
    create_sample_data(temp_data_dir)
    archive_path = backup_service.create_backup_archive()
    assert archive_path.exists()

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert 'orders_manager.db' in names
    assert 'settings.json' in names
    assert 'uploads/image.png' in names

    archive_path.unlink()


def test_restore_backup_round_trip(temp_data_dir, reset_backup_module):
    create_sample_data(temp_data_dir)
    archive_path = backup_service.create_backup_archive()

    # Modify current data to ensure it gets replaced
    (temp_data_dir / 'orders_manager.db').write_text('modified')
    (temp_data_dir / 'extra.txt').write_text('remove me')

    with archive_path.open('rb') as handle:
        backup_service.restore_backup_from_stream(handle)

    assert (temp_data_dir / 'orders_manager.db').read_text() == 'db'
    assert not (temp_data_dir / 'extra.txt').exists()

    archive_path.unlink()


def test_export_endpoint_returns_zip(temp_data_dir, monkeypatch, reset_backup_module):
    create_sample_data(temp_data_dir)

    client = firecoast_app.app.test_client()
    response = client.get('/api/export-data')
    assert response.status_code == 200
    assert response.mimetype == 'application/zip'

    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert 'orders_manager.db' in archive.namelist()


def test_import_endpoint_restores_data(temp_data_dir, monkeypatch, reset_backup_module):
    client = firecoast_app.app.test_client()

    # Prepare original data
    create_sample_data(temp_data_dir)
    archive_path = backup_service.create_backup_archive()

    # Overwrite with garbage that should be replaced
    for item in temp_data_dir.iterdir():
        if item.is_dir():
            import shutil
            shutil.rmtree(item)
        else:
            item.unlink()
    (temp_data_dir / 'orders_manager.db').write_text('outdated')

    with archive_path.open('rb') as fh:
        payload = {
            'file': (io.BytesIO(fh.read()), 'backup.zip'),
        }

    monkeypatch.setattr(firecoast_app, 'init_db', lambda: None)
    response = client.post('/api/import-data', data=payload, content_type='multipart/form-data')
    assert response.status_code == 200
    assert (temp_data_dir / 'orders_manager.db').read_text() == 'db'

    archive_path.unlink()
