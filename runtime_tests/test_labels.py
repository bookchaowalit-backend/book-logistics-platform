"""Label replay: one shipment never buys two labels; ambiguous carrier outcomes are quarantined."""
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from conftest import SHIPMENT

from platform_logistics.carriers import CarrierError, SandboxCarrier
from platform_logistics.store import (
    InvalidTransition,
    LabelInProgress,
    LabelRejected,
    LabelUncertain,
    LogisticsStore,
)


def setup(database, carrier=None):
    carrier = carrier or SandboxCarrier()
    s = LogisticsStore(database, {'sandbox': carrier})
    w = uuid4()
    row, _ = s.create_shipment(w, 'k1', SHIPMENT)
    return s, carrier, w, row['id']


def expire_lease(database, workspace, shipment_id):
    with database.transaction(workspace) as c:
        c.execute("UPDATE shipment_label SET lease_until=clock_timestamp()-interval '1 second' WHERE shipment_id=%s",
                  (shipment_id,))


def test_purchase_then_replay_returns_the_same_label(database):
    s, carrier, w, sid = setup(database)
    outcome, shipment = s.purchase_label(w, sid)
    assert outcome == 'purchased' and shipment['status'] == 'labelled'
    assert shipment['label']['status'] == 'purchased' and shipment['label']['tracking_number'].startswith('SBX')
    again, replay = s.purchase_label(w, sid)
    assert again == 'replayed' and replay['label'] == shipment['label']
    assert carrier.purchases == 1


def test_concurrent_purchases_buy_one_label(database):
    gate = threading.Event()

    class Slow(SandboxCarrier):
        def purchase_label(self, shipment, key):
            gate.wait(5)
            return super().purchase_label(shipment, key)
    s, carrier, w, sid = setup(database, Slow())

    def attempt(_):
        try:
            return s.purchase_label(w, sid)[0]
        except LabelInProgress:
            return 'in_progress'
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(attempt, i) for i in range(4)]
        # Let the losers observe the persisted purchasing lease before the winner finishes.
        threading.Timer(0.5, gate.set).start()
        outcomes = sorted(f.result() for f in futures)
    assert outcomes.count('purchased') == 1 and set(outcomes) <= {'purchased', 'in_progress', 'replayed'}
    assert carrier.purchases == 1


def test_uncertain_after_effect_is_quarantined_until_reconciled(database):
    s, carrier, w, sid = setup(database, SandboxCarrier(script=['uncertain_after']))
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, sid)
    shipment = s.get_shipment(w, sid)
    assert shipment['status'] == 'label_uncertain' and shipment['label'] == {
        'status': 'uncertain', 'tracking_number': None, 'label_ref': None}
    calls = []
    original = carrier.purchase_label
    carrier.purchase_label = lambda *a: calls.append(a) or original(*a)
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, sid)
    assert calls == [] and carrier.purchases == 1
    state, shipment = s.reconcile_label(w, sid)
    assert state == 'purchased' and shipment['status'] == 'labelled'
    assert shipment['label']['tracking_number'] == carrier.find_label('label/' + str(sid)).tracking_number
    assert s.purchase_label(w, sid)[0] == 'replayed' and carrier.purchases == 1


def test_uncertain_without_effect_reconciles_to_retry(database):
    s, carrier, w, sid = setup(database, SandboxCarrier(script=['uncertain_before']))
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, sid)
    state, shipment = s.reconcile_label(w, sid)
    assert state == 'failed' and shipment['status'] == 'created'
    assert s.purchase_label(w, sid)[0] == 'purchased' and carrier.purchases == 1


def test_lookup_failure_keeps_quarantine(database):
    s, _, w, sid = setup(database, SandboxCarrier(script=['uncertain_before', 'lookup_uncertain']))
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, sid)
    with pytest.raises(LabelUncertain):
        s.reconcile_label(w, sid)
    assert s.get_shipment(w, sid)['label']['status'] == 'uncertain'


@pytest.mark.parametrize('outcome', ['rejected', 'unavailable'])
def test_definite_rejection_allows_a_retry(database, outcome):
    s, carrier, w, sid = setup(database, SandboxCarrier(script=[outcome]))
    with pytest.raises(LabelRejected) as exc:
        s.purchase_label(w, sid)
    assert exc.value.outcome == outcome
    shipment = s.get_shipment(w, sid)
    assert shipment['status'] == 'created' and shipment['label']['status'] == 'failed'
    assert s.purchase_label(w, sid)[0] == 'purchased' and carrier.purchases == 1
    with database.transaction(w) as c:
        assert c.execute('SELECT attempts FROM shipment_label').fetchone()[0] == 2


def test_unexpected_adapter_error_is_uncertain(database):
    class Broken(SandboxCarrier):
        def purchase_label(self, shipment, key):
            raise RuntimeError('SYNTHETIC_PRIVATE_DETAIL')
    s, _, w, sid = setup(database, Broken())
    with pytest.raises(LabelUncertain) as exc:
        s.purchase_label(w, sid)
    assert 'PRIVATE' not in str(exc.value)


def test_malformed_carrier_label_is_uncertain(database):
    class Malformed(SandboxCarrier):
        def purchase_label(self, shipment, key):
            return object()
    s, _, w, sid = setup(database, Malformed())
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, sid)


def test_crash_after_persisting_purchasing_becomes_uncertain(database):
    s, carrier, w, sid = setup(database)
    lease = s.begin_label(w, sid)
    assert s.get_shipment(w, sid)['status'] == 'label_pending'
    with pytest.raises(LabelInProgress):
        s.purchase_label(w, sid)
    expire_lease(database, w, sid)
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, sid)
    assert carrier.purchases == 0 and s.get_shipment(w, sid)['status'] == 'label_uncertain'
    # The crashed request's late completion cannot overwrite the quarantine.
    assert not s.finish_label(w, sid, lease, 'purchased', carrier.purchase_label({}, 'label/' + str(sid)))


def test_stale_or_foreign_lease_cannot_finish(database):
    s, _, w, sid = setup(database)
    lease = s.begin_label(w, sid)
    assert not s.finish_label(w, sid, uuid4(), 'rejected')
    expire_lease(database, w, sid)
    assert not s.finish_label(w, sid, lease, 'rejected')


def test_label_needs_an_open_shipment_in_the_callers_workspace(database):
    s, carrier, w, sid = setup(database)
    assert s.purchase_label(uuid4(), sid) is None
    s.cancel(w, sid)
    with pytest.raises(InvalidTransition):
        s.purchase_label(w, sid)
    assert carrier.purchases == 0


def test_carrier_error_outcomes_are_normalized():
    assert CarrierError('anything').outcome == 'uncertain'
