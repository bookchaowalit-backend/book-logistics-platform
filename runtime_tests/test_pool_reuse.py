"""A single pooled connection stays usable and unscoped after conflicts, quarantine and foreign reads."""
from uuid import uuid4

import pytest
from conftest import SHIPMENT
from platform_backend_core import Database

from platform_logistics.carriers import SandboxCarrier
from platform_logistics.store import Conflict, LabelUncertain, LogisticsStore


@pytest.fixture
def pooled(temp_db, database):
    db = Database(temp_db.runtime_dsn, min_size=1, max_size=1)
    yield db
    db.close()


def test_one_connection_survives_errors_and_keeps_workspaces_apart(pooled):
    s = LogisticsStore(pooled, {'sandbox': SandboxCarrier(script=['uncertain_after'])})
    w, foreign = uuid4(), uuid4()
    for _ in range(2):
        assert pooled.ready() and s.ready()
    row, _ = s.create_shipment(w, 'k1', SHIPMENT)
    with pytest.raises(Conflict):
        s.create_shipment(w, 'k1', SHIPMENT | {'service': 'express'})
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, row['id'])
    with pytest.raises(LabelUncertain):
        s.purchase_label(w, row['id'])
    assert s.get_shipment(foreign, row['id']) is None and s.list_shipments(foreign) == []
    assert s.reconcile_label(w, row['id'])[0] == 'purchased'
    for _ in range(3):
        assert pooled.ready() and s.ready()
    with pooled.transaction(uuid4()) as c:
        assert c.execute('SELECT count(*) FROM shipment').fetchone()[0] == 0
