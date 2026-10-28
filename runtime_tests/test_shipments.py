"""Shipments: idempotent creation, deterministic stored rate, workspace isolation, cancel."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from conftest import SHIPMENT

from platform_logistics.carriers import SandboxCarrier
from platform_logistics.store import Conflict, InvalidTransition, LogisticsStore


def store(database, carrier=None):
    return LogisticsStore(database, {'sandbox': carrier or SandboxCarrier()})


def test_concurrent_create_with_one_key_makes_one_shipment(database):
    s = store(database)
    w = uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: s.create_shipment(w, 'order-1001', SHIPMENT), range(4)))
    assert len({row['id'] for row, _ in results}) == 1
    assert sorted(created for _, created in results) == [False, False, False, True]
    assert len(s.list_shipments(w)) == 1


def test_changed_payload_under_same_key_conflicts(database):
    s = store(database)
    w = uuid4()
    s.create_shipment(w, 'k1', SHIPMENT)
    with pytest.raises(Conflict):
        s.create_shipment(w, 'k1', SHIPMENT | {'service': 'express'})
    with pytest.raises(Conflict):
        s.create_shipment(w, 'k1', SHIPMENT | {'parcel': SHIPMENT['parcel'] | {'weight_grams': 401}})
    # Key order in the request does not change the fingerprint.
    row, created = s.create_shipment(w, 'k1', dict(reversed(list(SHIPMENT.items()))))
    assert not created and row['service'] == 'standard'


def test_stored_rate_is_the_deterministic_quote(database):
    s = store(database)
    w = uuid4()
    row, created = s.create_shipment(w, 'k1', SHIPMENT | {'service': 'express'})
    assert created and row['status'] == 'created' and row['label'] is None
    assert row['rate'] == {'amount_minor': 8000, 'currency': 'THB', 'billable_weight_grams': 500}
    assert row['origin'] == SHIPMENT['origin'] and row['parcel'] == SHIPMENT['parcel']
    quotes = s.quote(SHIPMENT['origin'], SHIPMENT['destination'], SHIPMENT['parcel'])
    assert [q['amount_minor'] for q in quotes] == [4000, 8000]


@pytest.mark.parametrize('change', [
    {'carrier': 'unknown'}, {'service': 'overnight'}, {'reference': ''}, {'reference': 'x' * 65},
    {'recipient_ref': 'Jane Doe, 1 Main St'}, {'origin': {'country': 'th', 'postal_code': '10110'}},
    {'parcel': {'weight_grams': 0, 'length_mm': 1, 'width_mm': 1, 'height_mm': 1}}, {'extra': 'x'},
])
def test_invalid_shipments_are_rejected(database, change):
    with pytest.raises(ValueError):
        store(database).create_shipment(uuid4(), 'k1', SHIPMENT | change)


def test_invalid_idempotency_key_is_rejected(database):
    for key in ('', 'x' * 129, 'has space', None):
        with pytest.raises(ValueError):
            store(database).create_shipment(uuid4(), key, SHIPMENT)


def test_workspaces_are_isolated_and_rls_rejects_foreign_rows(database):
    s = store(database)
    w, other = uuid4(), uuid4()
    row, _ = s.create_shipment(w, 'k1', SHIPMENT)
    assert s.get_shipment(other, row['id']) is None and s.list_shipments(other) == []
    # The same key in another workspace is a different shipment.
    assert s.create_shipment(other, 'k1', SHIPMENT)[0]['id'] != row['id']
    with database.transaction(w) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE shipment SET status='created' WHERE true")
        conn.execute("INSERT INTO shipment(workspace_id,id,idempotency_key,fingerprint,reference,recipient_ref,carrier,service,"
                     "origin_country,origin_postal_code,destination_country,destination_postal_code,weight_grams,length_mm,"
                     "width_mm,height_mm,billable_weight_grams,rate_amount_minor,currency) VALUES "
                     "(%s,%s,'foreign','fixture','r','c','sandbox','standard','TH','10110','TH','10110',1,1,1,1,500,1,'THB')",
                     (other, uuid4()))


def test_list_pages_by_id(database):
    s = store(database)
    w = uuid4()
    ids = sorted(s.create_shipment(w, f'k{i}', SHIPMENT)[0]['id'] for i in range(3))
    assert [r['id'] for r in s.list_shipments(w, limit=2)] == ids[:2]
    assert [r['id'] for r in s.list_shipments(w, limit=2, after=ids[1])] == ids[2:]
    with pytest.raises(ValueError):
        s.list_shipments(w, limit=0)


def test_cancel_only_from_created(database):
    s = store(database)
    w = uuid4()
    row, _ = s.create_shipment(w, 'k1', SHIPMENT)
    assert s.cancel(w, row['id'])['status'] == 'cancelled'
    with pytest.raises(InvalidTransition):
        s.cancel(w, row['id'])
    assert s.cancel(uuid4(), row['id']) is None
