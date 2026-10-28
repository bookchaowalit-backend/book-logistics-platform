"""Only domain validation maps to 422; any other ValueError is a server bug (redacted 500).

Offline: the store is replaced or fails before touching the database.
"""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from conftest import SHIPMENT, auth
from fastapi.testclient import TestClient

from platform_logistics import rules
from platform_logistics.app import create_app
from platform_logistics.carriers import SandboxCarrier
from platform_logistics.store import InvalidInput, LogisticsStore

ALL = 'rates:read shipments:read shipments:write labels:write tracking:write'
EVENT = {'carrier': 'sandbox', 'carrier_event_id': 'ev-1', 'tracking_number': 'TRK1', 'status': 'in_transit',
         'occurred_at': datetime(2026, 10, 20, tzinfo=UTC)}


class _NoDatabase:
    def ready(self):
        return False

    def close(self):
        pass

    def transaction(self, workspace):
        raise AssertionError('database must not be reached')


@pytest.fixture
def client():
    verifier, headers = auth()
    with TestClient(create_app(_NoDatabase(), verifier, carriers={'sandbox': SandboxCarrier()})) as c:
        yield c, headers(ALL, uuid4())


def _store():
    return LogisticsStore(_NoDatabase(), {'sandbox': SandboxCarrier()})


def test_invalid_input_is_a_value_error():
    assert issubclass(InvalidInput, ValueError)


@pytest.mark.parametrize('call', [
    lambda: rules.validate_parcel(SHIPMENT['parcel'] | {'weight_grams': 0}),
    lambda: rules.validate_address({'country': 'th', 'postal_code': '10110'}),
    lambda: rules.billable_weight_grams({}),
    lambda: rules.tracking_target('labelled', 'teleported'),
    lambda: _store().carrier('nope'),
    lambda: _store().validate_shipment(SHIPMENT | {'reference': 'bad ref!'}),
    lambda: _store().validate_shipment(SHIPMENT | {'service': 'teleport'}),
    lambda: _store().create_shipment(uuid4(), 'bad key!', SHIPMENT),
    lambda: _store().list_shipments(uuid4(), 0),
    lambda: _store().validate_event(EVENT | {'tracking_number': 'bad number!'}),
    lambda: _store().validate_event(EVENT | {'occurred_at': datetime(1999, 1, 1, tzinfo=UTC)}),
])
def test_validation_raises_invalid_input(call):
    with pytest.raises(InvalidInput):
        call()


@pytest.mark.parametrize('call', [
    lambda: _store().begin_label(uuid4(), uuid4(), 1),
    lambda: _store().finish_label(uuid4(), uuid4(), uuid4(), 'made-up'),
])
def test_internal_argument_errors_stay_plain_value_errors(call):
    # Lease length and label outcome are set by the service itself, never by a client: not 422.
    with pytest.raises(ValueError) as caught:
        call()
    assert not isinstance(caught.value, InvalidInput)


def test_domain_validation_returns_422(client):
    c, h = client
    body = {k: SHIPMENT[k] for k in ('origin', 'destination', 'parcel')}
    response = c.post('/v1/rates', headers=h, json=body | {'parcel': SHIPMENT['parcel'] | {'weight_grams': 0}})
    assert response.status_code == 422
    assert response.json() == {'detail': 'Invalid request'}
    assert c.post('/v1/shipments', headers=h, json=SHIPMENT | {'reference': 'bad ref!'}).status_code == 422
    assert c.post('/v1/shipments', headers=h, json=SHIPMENT | {'carrier': 'nope'}).status_code == 422
    event = EVENT | {'carrier': 'nope', 'occurred_at': '2026-10-20T08:00:00+00:00'}
    assert c.post('/v1/tracking-events', headers=h, json=event).status_code == 422


def test_unexpected_value_error_is_redacted_500(client, monkeypatch):
    def broken(self, workspace, limit=100, after=None):
        raise ValueError('secret-bearing internal detail')

    monkeypatch.setattr(LogisticsStore, 'list_shipments', broken)
    c, h = client
    response = c.get('/v1/shipments', headers=h)
    assert response.status_code == 500
    assert 'secret-bearing' not in response.text
    assert response.json() == {'detail': 'internal server error'}
