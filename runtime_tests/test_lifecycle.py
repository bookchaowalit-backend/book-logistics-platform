"""Explicit lifecycle commands: migrate is separate from serve, and failures stay generic."""
import sys

import pytest
from conftest import admin_dsn
from platform_backend_core.testing import temporary_database


def run(monkeypatch, *args):
    from platform_logistics.__main__ import main
    monkeypatch.setattr(sys, 'argv', ['platform_logistics', *args])
    main()


def test_startup_failure_is_generic(monkeypatch, capsys):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://SYNTHETIC_USER@invalid.invalid/none')
    monkeypatch.delenv('JWT_ISSUER', raising=False)
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, 'serve')
    assert str(exc.value) == 'Service configuration or dependency unavailable'
    assert 'SYNTHETIC' not in capsys.readouterr().err


def test_serve_refuses_without_a_carrier(monkeypatch):
    monkeypatch.setenv('JWT_ISSUER', 'fixture')
    monkeypatch.setenv('JWT_AUDIENCE', 'fixture')
    monkeypatch.setenv('JWT_JWKS_URL', 'https://issuer.invalid/jwks')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fixture@invalid.invalid/none')
    monkeypatch.delenv('LOGISTICS_CARRIERS', raising=False)
    with pytest.raises(SystemExit):
        run(monkeypatch, 'serve')


def test_migrate_applies_the_logistics_namespace(monkeypatch):
    import psycopg
    with temporary_database(admin_dsn(), prefix='logm_') as temp:
        monkeypatch.setenv('DATABASE_URL', temp.admin_dsn)
        run(monkeypatch, 'migrate')
        run(monkeypatch, 'migrate')
        with psycopg.connect(temp.admin_dsn) as conn:
            assert conn.execute("SELECT version FROM platform_schema_migrations WHERE namespace='logistics'").fetchall() == [('0001',)]
