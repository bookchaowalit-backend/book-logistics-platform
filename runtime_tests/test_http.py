"""HTTP surface: workspace JWT scopes, idempotency, generic errors, readiness and privileges."""
import logging
from uuid import uuid4

import psycopg
import pytest
from conftest import SHIPMENT, auth
from fastapi.testclient import TestClient
from platform_backend_core import Database, MigrationRunner
from psycopg import sql

from platform_logistics.app import create_app
from platform_logistics.carriers import SandboxCarrier
from platform_logistics.migrations import MIGRATIONS

ALL = 'rates:read shipments:read shipments:write labels:write tracking:write'


@pytest.fixture
def client(database):
    verifier, headers = auth()
    carrier = SandboxCarrier()
    with TestClient(create_app(database, verifier, carriers={'sandbox': carrier})) as c:
        yield c, headers, carrier


def test_missing_schema_or_carrier_is_not_ready(temp_db):
    verifier, _ = auth()

    def ready(carriers):
        # The shared app closes its database at shutdown, so each client gets a fresh pool.
        with TestClient(create_app(Database(temp_db.runtime_dsn), verifier, carriers=carriers)) as c:
            assert c.get('/healthz').status_code == 200
            return c.get('/readyz').status_code
    assert ready({'sandbox': SandboxCarrier()}) == 503
    MigrationRunner(temp_db.admin_dsn, namespace='logistics').apply(MIGRATIONS)
    temp_db.grant_schema('public')
    assert ready({'sandbox': SandboxCarrier()}) == 200
    assert ready({}) == 503
    with psycopg.connect(temp_db.admin_dsn) as admin:
        admin.execute('DELETE FROM platform_schema_migrations WHERE namespace=%s', ('logistics',))
    assert ready({'sandbox': SandboxCarrier()}) == 503


PRIVILEGES = [('TABLE', t, p) for t, ps in (('shipment', 'SELECT INSERT UPDATE'), ('shipment_label', 'SELECT INSERT UPDATE'),
                                           ('tracking_event', 'SELECT INSERT')) for p in ps.split()]
PRIVILEGES += [('SCHEMA', 'public', 'USAGE'), ('TABLE', 'platform_schema_migrations', 'SELECT')]


@pytest.mark.parametrize('kind,target,privilege', PRIVILEGES)
def test_readiness_requires_each_effective_privilege(temp_db, database, kind, target, privilege):
    verifier, _ = auth()
    with psycopg.connect(temp_db.admin_dsn) as admin:
        admin.execute('REVOKE USAGE ON SCHEMA public FROM PUBLIC')
    grant = sql.SQL('{} {} ON {} {} {} {}')
    with TestClient(create_app(database, verifier, carriers={'sandbox': SandboxCarrier()})) as c:
        assert c.get('/readyz').status_code == 200
        with psycopg.connect(temp_db.admin_dsn) as admin:
            admin.execute(grant.format(sql.SQL('REVOKE'), sql.SQL(privilege), sql.SQL(kind), sql.Identifier(target),
                                       sql.SQL('FROM'), sql.Identifier(temp_db.runtime_role)))
        assert c.get('/readyz').status_code == 503
        with psycopg.connect(temp_db.admin_dsn) as admin:
            admin.execute(grant.format(sql.SQL('GRANT'), sql.SQL(privilege), sql.SQL(kind), sql.Identifier(target),
                                       sql.SQL('TO'), sql.Identifier(temp_db.runtime_role)))
        assert c.get('/readyz').status_code == 200


def test_rates_need_scope_and_are_deterministic(client):
    c, headers, _ = client
    body = {k: SHIPMENT[k] for k in ('origin', 'destination', 'parcel')}
    assert c.post('/v1/rates', json=body).status_code == 401
    assert c.post('/v1/rates', headers=headers('shipments:read'), json=body).status_code == 403
    first = c.post('/v1/rates', headers=headers('rates:read'), json=body)
    assert first.status_code == 200 and first.json() == c.post('/v1/rates', headers=headers('rates:read'), json=body).json()
    assert [(r['service'], r['amount_minor']) for r in first.json()['rates']] == [('standard', 4000), ('express', 8000)]
    assert c.post('/v1/rates', headers=headers('rates:read'), json=body | {'parcel': {'weight_grams': 0}}).status_code == 422


def test_shipment_lifecycle_over_http(client, caplog):
    caplog.set_level(logging.DEBUG)
    c, headers, carrier = client
    w = uuid4()
    h = headers(ALL, w, key_header='order-1001')
    created = c.post('/v1/shipments', headers=h, json=SHIPMENT)
    assert created.status_code == 201 and created.json()['status'] == 'created'
    sid = created.json()['id']
    replay = c.post('/v1/shipments', headers=h, json=SHIPMENT)
    assert replay.status_code == 200 and replay.json()['id'] == sid
    assert c.post('/v1/shipments', headers=h, json=SHIPMENT | {'service': 'express'}).status_code == 409
    assert c.post('/v1/shipments', headers=headers(ALL, w, key_header=None), json=SHIPMENT).status_code == 422
    assert c.post('/v1/shipments', headers=headers('shipments:read', w), json=SHIPMENT).status_code == 403
    assert c.get('/v1/shipments/' + sid, headers=headers(ALL)).status_code == 404
    assert [s['id'] for s in c.get('/v1/shipments', headers=h).json()] == [sid]

    label = c.post(f'/v1/shipments/{sid}/label', headers=h)
    assert label.status_code == 201 and label.json()['status'] == 'labelled'
    again = c.post(f'/v1/shipments/{sid}/label', headers=h)
    assert again.status_code == 200 and again.json()['label'] == label.json()['label'] and carrier.purchases == 1
    assert c.post(f'/v1/shipments/{sid}/cancel', headers=h).status_code == 409

    tracking = label.json()['label']['tracking_number']
    ev = {'carrier': 'sandbox', 'carrier_event_id': 'ev-1', 'tracking_number': tracking, 'status': 'in_transit',
          'occurred_at': '2026-10-20T08:00:00Z'}
    first = c.post('/v1/tracking-events', headers=h, json=ev)
    assert first.status_code == 201 and first.json() == {'outcome': 'applied', 'shipment_id': sid, 'status': 'in_transit'}
    assert c.post('/v1/tracking-events', headers=h, json=ev).json()['outcome'] == 'duplicate'
    assert c.post('/v1/tracking-events', headers=h, json=ev | {'status': 'delivered'}).status_code == 409
    assert c.post('/v1/tracking-events', headers=h, json=ev | {'occurred_at': '2026-10-20T08:00:00'}).status_code == 422
    assert c.post('/v1/tracking-events', headers=headers(ALL), json=ev).status_code == 404
    assert c.post('/v1/tracking-events', headers=headers('shipments:write', w), json=ev).status_code == 403
    events = c.get(f'/v1/shipments/{sid}/events', headers=h).json()
    assert [(e['carrier_event_id'], e['outcome']) for e in events] == [('ev-1', 'applied')]
    # Requests never echo or log addresses, references or fixture values.
    leaked = c.post('/v1/shipments', headers=h, json=SHIPMENT | {'secret': 'SYNTHETIC_VALUE'})
    assert leaked.status_code == 422 and 'SYNTHETIC_VALUE' not in leaked.text
    logged = caplog.text
    assert all(v not in logged for v in ('50000', '10110', 'contact:fixture-1', 'order-1001', tracking))


def test_uncertain_label_over_http_is_quarantined_then_reconciled(database):
    verifier, headers = auth()
    carrier = SandboxCarrier(script=['uncertain_after'])
    with TestClient(create_app(database, verifier, carriers={'sandbox': carrier})) as c:
        h = headers(ALL, uuid4())
        sid = c.post('/v1/shipments', headers=h, json=SHIPMENT).json()['id']
        first = c.post(f'/v1/shipments/{sid}/label', headers=h)
        assert first.status_code == 409 and first.json() == {'detail': 'Label outcome uncertain'}
        assert c.post(f'/v1/shipments/{sid}/label', headers=h).status_code == 409 and carrier.purchases == 1
        settled = c.post(f'/v1/shipments/{sid}/label/reconcile', headers=h)
        assert settled.status_code == 200 and settled.json()['status'] == 'labelled'
        assert c.post(f'/v1/shipments/{sid}/label', headers=h).status_code == 200 and carrier.purchases == 1


@pytest.mark.parametrize('outcome,status', [('rejected', 422), ('unavailable', 503)])
def test_definite_carrier_failures_over_http(database, outcome, status):
    verifier, headers = auth()
    with TestClient(create_app(database, verifier, carriers={'sandbox': SandboxCarrier(script=[outcome])})) as c:
        h = headers(ALL, uuid4())
        sid = c.post('/v1/shipments', headers=h, json=SHIPMENT).json()['id']
        failed = c.post(f'/v1/shipments/{sid}/label', headers=h)
        assert failed.status_code == status and 'sandbox' not in failed.text
        assert c.post(f'/v1/shipments/{sid}/label', headers=h).status_code == 201


def test_cancel_and_unknown_ids(client):
    c, headers, _ = client
    h = headers(ALL, uuid4())
    sid = c.post('/v1/shipments', headers=h, json=SHIPMENT).json()['id']
    assert c.post(f'/v1/shipments/{sid}/cancel', headers=h).json()['status'] == 'cancelled'
    assert c.post(f'/v1/shipments/{sid}/label', headers=h).status_code == 409
    for path in ('', '/label', '/cancel', '/label/reconcile', '/events'):
        method = c.get if path in ('', '/events') else c.post
        assert method(f'/v1/shipments/{uuid4()}{path}', headers=h).status_code == 404
    assert c.get('/v1/shipments/not-a-uuid', headers=h).status_code == 422
