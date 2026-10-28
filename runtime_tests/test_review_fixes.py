"""Review fixes: unconfigured carriers never quarantine, reconcile collisions, extreme timestamps."""
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from conftest import SHIPMENT

from platform_logistics.carriers import Label, SandboxCarrier
from platform_logistics.store import LabelUncertain, LogisticsStore


class Off(SandboxCarrier):
    configured = False


def test_unconfigured_carrier_is_rejected_before_any_lease(database):
    w = uuid4()
    row, _ = LogisticsStore(database, {'sandbox': SandboxCarrier()}).create_shipment(w, 'k1', SHIPMENT)
    later = LogisticsStore(database, {'sandbox': Off()})
    with pytest.raises(ValueError):
        later.purchase_label(w, row['id'])
    shipment = later.get_shipment(w, row['id'])
    assert shipment['label'] is None and shipment['status'] == 'created'


def test_reconcile_with_an_unconfigured_carrier_is_rejected_not_uncertain(database):
    w = uuid4()
    s = LogisticsStore(database, {'sandbox': SandboxCarrier(script=['uncertain_before'])})
    row, _ = s.create_shipment(w, 'k1', SHIPMENT)
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, row['id'])
    with pytest.raises(ValueError):
        LogisticsStore(database, {'sandbox': Off()}).reconcile_label(w, row['id'])
    assert s.get_shipment(w, row['id'])['label']['status'] == 'uncertain'


class Colliding(SandboxCarrier):
    def __init__(self, tracking):
        super().__init__(script=['uncertain_before'])
        self.tracking = tracking

    def find_label(self, idempotency_key):
        return Label(self.tracking, 'label-collision')


def test_reconcile_tracking_collision_stays_uncertain(database):
    w = uuid4()
    first = LogisticsStore(database, {'sandbox': SandboxCarrier()})
    a, _ = first.create_shipment(w, 'a', SHIPMENT)
    _, labelled = first.purchase_label(w, a['id'])
    s = LogisticsStore(database, {'sandbox': Colliding(labelled['label']['tracking_number'])})
    b, _ = s.create_shipment(w, 'b', SHIPMENT)
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, b['id'])
    with pytest.raises(LabelUncertain):
        s.reconcile_label(w, b['id'])
    assert s.get_shipment(w, b['id'])['label']['status'] == 'uncertain'


@pytest.mark.parametrize('at', [datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5))),
                                datetime(1, 1, 1, tzinfo=UTC)])
def test_extreme_or_ancient_event_times_are_invalid(database, at):
    s = LogisticsStore(database, {'sandbox': SandboxCarrier()})
    event = {'carrier': 'sandbox', 'carrier_event_id': 'evt-1', 'tracking_number': 'SBX0000000001',
             'status': 'in_transit', 'occurred_at': at}
    with pytest.raises(ValueError):
        s.validate_event(event)
