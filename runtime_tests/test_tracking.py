"""Tracking deduplication by carrier event id, ordered application and validated transitions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import SHIPMENT

from platform_logistics.carriers import SandboxCarrier
from platform_logistics.store import Conflict, LogisticsStore

T0 = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)


def labelled(database):
    s = LogisticsStore(database, {'sandbox': SandboxCarrier()})
    w = uuid4()
    row, _ = s.create_shipment(w, 'k1', SHIPMENT)
    _, shipment = s.purchase_label(w, row['id'])
    return s, w, shipment


def event(shipment, event_id, status, at):
    return {'carrier': 'sandbox', 'carrier_event_id': event_id, 'tracking_number': shipment['label']['tracking_number'],
            'status': status, 'occurred_at': at}


def test_event_applies_once_and_duplicates_change_nothing(database):
    s, w, shipment = labelled(database)
    outcome, current = s.record_tracking_event(w, event(shipment, 'ev-1', 'in_transit', T0))
    assert outcome == 'applied' and current['status'] == 'in_transit' and current['last_event_at'] == T0
    outcome, current = s.record_tracking_event(w, event(shipment, 'ev-1', 'in_transit', T0))
    assert outcome == 'duplicate' and current['status'] == 'in_transit'
    events = s.list_tracking_events(w, shipment['id'])
    assert [(e['carrier_event_id'], e['status'], e['outcome']) for e in events] == [('ev-1', 'in_transit', 'applied')]


def test_reused_event_id_with_other_content_conflicts(database):
    s, w, shipment = labelled(database)
    s.record_tracking_event(w, event(shipment, 'ev-1', 'in_transit', T0))
    with pytest.raises(Conflict):
        s.record_tracking_event(w, event(shipment, 'ev-1', 'delivered', T0))
    with pytest.raises(Conflict):
        s.record_tracking_event(w, event(shipment, 'ev-1', 'in_transit', T0 + timedelta(seconds=1)))


def test_concurrent_duplicate_deliveries_store_one_event(database):
    s, w, shipment = labelled(database)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = sorted(pool.map(lambda _: s.record_tracking_event(w, event(shipment, 'ev-1', 'in_transit', T0))[0], range(4)))
    assert outcomes == ['applied', 'duplicate', 'duplicate', 'duplicate']
    assert len(s.list_tracking_events(w, shipment['id'])) == 1


def test_late_older_event_is_recorded_without_regressing_status(database):
    s, w, shipment = labelled(database)
    assert s.record_tracking_event(w, event(shipment, 'ev-2', 'delivered', T0 + timedelta(hours=5)))[0] == 'applied'
    outcome, current = s.record_tracking_event(w, event(shipment, 'ev-1', 'in_transit', T0))
    assert outcome == 'recorded' and current['status'] == 'delivered'
    assert [e['carrier_event_id'] for e in s.list_tracking_events(w, shipment['id'])] == ['ev-1', 'ev-2']


def test_newer_event_that_breaks_the_graph_is_recorded_only(database):
    s, w, shipment = labelled(database)
    s.record_tracking_event(w, event(shipment, 'ev-1', 'delivered', T0))
    outcome, current = s.record_tracking_event(w, event(shipment, 'ev-2', 'returned', T0 + timedelta(hours=1)))
    assert outcome == 'recorded' and current['status'] == 'delivered'


def test_exception_recovers_through_the_graph(database):
    s, w, shipment = labelled(database)
    steps = [('in_transit', 'in_transit'), ('exception', 'exception'), ('in_transit', 'in_transit'),
             ('out_for_delivery', 'out_for_delivery'), ('delivered', 'delivered')]
    for i, (status, expected) in enumerate(steps):
        outcome, current = s.record_tracking_event(w, event(shipment, f'ev-{i}', status, T0 + timedelta(hours=i)))
        assert outcome == 'applied' and current['status'] == expected


def test_unknown_or_foreign_tracking_numbers_are_not_found(database):
    s, w, shipment = labelled(database)
    assert s.record_tracking_event(uuid4(), event(shipment, 'ev-1', 'in_transit', T0)) is None
    unknown = event(shipment, 'ev-1', 'in_transit', T0) | {'tracking_number': 'SBX000000000000'}
    assert s.record_tracking_event(w, unknown) is None
    assert s.list_tracking_events(uuid4(), shipment['id']) is None
    # An unlabelled shipment has no tracking number, so no event can reach it.
    row, _ = s.create_shipment(w, 'k2', SHIPMENT)
    assert s.list_tracking_events(w, row['id']) == []


@pytest.mark.parametrize('change', [
    {'status': 'label_pending'}, {'status': 'unknown'}, {'occurred_at': datetime(2026, 10, 20, 8, 0)},
    {'occurred_at': '2026-10-20T08:00:00Z'}, {'carrier_event_id': ''}, {'carrier_event_id': 'has space'},
    {'carrier': 'unknown'}, {'tracking_number': 'x' * 129}, {'extra': 1},
])
def test_invalid_events_are_rejected(database, change):
    s, w, shipment = labelled(database)
    with pytest.raises(ValueError):
        s.record_tracking_event(w, event(shipment, 'ev-1', 'in_transit', T0) | change)


def test_events_far_in_the_future_are_rejected(database):
    s, w, shipment = labelled(database)
    with pytest.raises(ValueError):
        s.record_tracking_event(w, event(shipment, 'ev-1', 'in_transit', datetime.now(UTC) + timedelta(days=2)))
