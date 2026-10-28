"""Workspace-scoped logistics state. Every query runs inside Database.transaction(workspace)."""
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from .carriers import CarrierError, Label
from .migrations import MIGRATIONS, TABLES
from .rules import can_transition, tracking_target, validate_address, validate_parcel

TRACKING_NUMBER = re.compile(r'[A-Za-z0-9_-]{1,128}')
LABEL_REF = re.compile(r'[A-Za-z0-9_.:/-]{1,256}')
LEASE_SECONDS = 60
KEY = re.compile(r'[A-Za-z0-9_.:/-]{1,128}')
REFERENCE = re.compile(r'[A-Za-z0-9_.:/-]{1,64}')
# An opaque pointer to a contact another platform owns; names and street addresses never land here.
RECIPIENT_REF = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
EVENT_FIELDS = {'carrier', 'carrier_event_id', 'tracking_number', 'status', 'occurred_at'}
EVENT_COLUMNS = 'carrier,carrier_event_id,status,occurred_at,outcome,received_at'
MAX_EVENT_SKEW = timedelta(days=1)
# Carrier events before this are malformed, never real tracking history.
MIN_EVENT_TIME = datetime(2000, 1, 1, tzinfo=UTC)
SHIPMENT_FIELDS = {'reference', 'recipient_ref', 'carrier', 'service', 'origin', 'destination', 'parcel'}
SHIPMENT_COLUMNS = ('s.id,s.reference,s.recipient_ref,s.carrier,s.service,s.origin_country,s.origin_postal_code,'
                    's.destination_country,s.destination_postal_code,s.weight_grams,s.length_mm,s.width_mm,s.height_mm,'
                    's.billable_weight_grams,s.rate_amount_minor,s.currency,s.status,s.last_event_at,s.created_at,s.updated_at,'
                    'l.status AS label_status,l.tracking_number,l.label_ref')
SELECT_SHIPMENT = (f'SELECT {SHIPMENT_COLUMNS} FROM shipment s LEFT JOIN shipment_label l '
                   'ON l.workspace_id=s.workspace_id AND l.shipment_id=s.id')


class Conflict(Exception):
    pass


class InvalidTransition(Exception):
    pass


class LabelInProgress(Exception):
    pass


class LabelUncertain(Exception):
    """The carrier may have bought a label; nothing buys another until reconcile settles it."""

    def __init__(self):
        super().__init__('Label outcome uncertain')


class LabelRejected(Exception):
    """The carrier definitely bought nothing (``rejected`` or ``unavailable``); a retry is safe."""

    def __init__(self, outcome):
        self.outcome = outcome
        super().__init__('Label rejected')


def valid_key(key):
    return isinstance(key, str) and bool(KEY.fullmatch(key))


def valid_label(label):
    return (isinstance(label, Label) and isinstance(label.tracking_number, str) and isinstance(label.label_ref, str)
            and bool(TRACKING_NUMBER.fullmatch(label.tracking_number)) and bool(LABEL_REF.fullmatch(label.label_ref)))


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def public_shipment(row):
    label = None
    if row['label_status'] is not None:
        label = {'status': row['label_status'], 'tracking_number': row['tracking_number'], 'label_ref': row['label_ref']}
    return {
        'id': row['id'], 'reference': row['reference'], 'recipient_ref': row['recipient_ref'],
        'carrier': row['carrier'], 'service': row['service'],
        'origin': {'country': row['origin_country'], 'postal_code': row['origin_postal_code']},
        'destination': {'country': row['destination_country'], 'postal_code': row['destination_postal_code']},
        'parcel': {'weight_grams': row['weight_grams'], 'length_mm': row['length_mm'],
                   'width_mm': row['width_mm'], 'height_mm': row['height_mm']},
        'rate': {'amount_minor': row['rate_amount_minor'], 'currency': row['currency'],
                 'billable_weight_grams': row['billable_weight_grams']},
        'status': row['status'], 'label': label, 'last_event_at': row['last_event_at'],
        'created_at': row['created_at'], 'updated_at': row['updated_at'],
    }


class LogisticsStore:
    def __init__(self, database, carriers):
        self.db = database
        self.carriers = dict(carriers)

    def carrier(self, name):
        carrier = self.carriers.get(name)
        if carrier is None or not carrier.configured:
            raise ValueError('Unknown carrier')
        return carrier

    def quote(self, origin, destination, parcel):
        rates = []
        for carrier in self.carriers.values():
            if carrier.configured:
                rates.extend(carrier.quote(origin, destination, parcel))
        return rates

    def validate_shipment(self, payload):
        if not isinstance(payload, dict) or set(payload) != SHIPMENT_FIELDS:
            raise ValueError('Invalid shipment')
        if not isinstance(payload['reference'], str) or not REFERENCE.fullmatch(payload['reference']):
            raise ValueError('Invalid shipment')
        if not isinstance(payload['recipient_ref'], str) or not RECIPIENT_REF.fullmatch(payload['recipient_ref']):
            raise ValueError('Invalid shipment')
        validate_address(payload['origin'])
        validate_address(payload['destination'])
        validate_parcel(payload['parcel'])
        rates = self.carrier(payload['carrier']).quote(payload['origin'], payload['destination'], payload['parcel'])
        rate = next((r for r in rates if r['service'] == payload['service']), None)
        if rate is None:
            raise ValueError('Invalid shipment')
        return rate

    def create_shipment(self, workspace, key, payload):
        """``(shipment, created)``. A replayed key returns the first shipment; a changed payload is Conflict."""
        if not valid_key(key):
            raise ValueError('Invalid key')
        rate = self.validate_shipment(payload)
        expected = fingerprint(payload)
        o, d, p = payload['origin'], payload['destination'], payload['parcel']
        with self.db.transaction(workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            created = c.execute(
                'INSERT INTO shipment(workspace_id,id,idempotency_key,fingerprint,reference,recipient_ref,carrier,service,'
                'origin_country,origin_postal_code,destination_country,destination_postal_code,weight_grams,length_mm,'
                'width_mm,height_mm,billable_weight_grams,rate_amount_minor,currency) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                'ON CONFLICT(workspace_id,idempotency_key) DO NOTHING RETURNING id',
                (workspace, uuid4(), key, expected, payload['reference'], payload['recipient_ref'], payload['carrier'],
                 payload['service'], o['country'], o['postal_code'], d['country'], d['postal_code'], p['weight_grams'],
                 p['length_mm'], p['width_mm'], p['height_mm'], rate['billable_weight_grams'], rate['amount_minor'],
                 rate['currency'])).fetchone() is not None
            row = c.execute(f'{SELECT_SHIPMENT} WHERE s.idempotency_key=%s', (key,)).fetchone()
            stored = c.execute('SELECT fingerprint FROM shipment WHERE idempotency_key=%s', (key,)).fetchone()
            if stored['fingerprint'] != expected:
                raise Conflict()
            return public_shipment(row), created

    def get_shipment(self, workspace, id):
        with self.db.transaction(workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            row = c.execute(f'{SELECT_SHIPMENT} WHERE s.id=%s', (id,)).fetchone()
            return None if row is None else public_shipment(row)

    def list_shipments(self, workspace, limit=100, after=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid limit')
        with self.db.transaction(workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            rows = c.execute(f'{SELECT_SHIPMENT} WHERE (%s::uuid IS NULL OR s.id>%s::uuid) ORDER BY s.id LIMIT %s',
                             (after, after, limit)).fetchall()
            return [public_shipment(r) for r in rows]

    def cancel(self, workspace, id):
        with self.db.transaction(workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            current = c.execute('SELECT status FROM shipment WHERE id=%s FOR UPDATE', (id,)).fetchone()
            if current is None:
                return None
            if not can_transition(current['status'], 'cancelled'):
                raise InvalidTransition()
            c.execute("UPDATE shipment SET status='cancelled',updated_at=clock_timestamp() WHERE id=%s", (id,))
            return public_shipment(c.execute(f'{SELECT_SHIPMENT} WHERE s.id=%s', (id,)).fetchone())

    @staticmethod
    def _expire_leases(c, id):
        """A purchasing lease that ran out may have reached the carrier: quarantine it as uncertain."""
        if c.execute("UPDATE shipment_label SET status='uncertain',lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() "
                     "WHERE shipment_id=%s AND status='purchasing' AND lease_until<=clock_timestamp() RETURNING shipment_id",
                     (id,)).fetchone():
            c.execute("UPDATE shipment SET status='label_uncertain',updated_at=clock_timestamp() "
                      "WHERE id=%s AND status='label_pending'", (id,))

    def begin_label(self, workspace, id, lease_seconds=LEASE_SECONDS):
        """Persist a purchasing lease before any carrier call; returns the lease token.

        Returns ``(state, shipment)`` instead of a token when no purchase may start: ``replayed``
        for a purchased label, or None for an unknown shipment."""
        if type(lease_seconds) is not int or not 10 <= lease_seconds <= 300:
            raise ValueError('Invalid lease')
        # Commit the quarantine on its own: the refusal below rolls back its transaction.
        with self.db.transaction(workspace) as c:
            self._expire_leases(c, id)
        with self.db.transaction(workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            shipment = c.execute('SELECT status FROM shipment WHERE id=%s FOR UPDATE', (id,)).fetchone()
            if shipment is None:
                return None
            label = c.execute('SELECT status FROM shipment_label WHERE shipment_id=%s', (id,)).fetchone()
            if label is not None and label['status'] == 'purchased':
                return 'replayed', public_shipment(c.execute(f'{SELECT_SHIPMENT} WHERE s.id=%s', (id,)).fetchone())
            if label is not None and label['status'] == 'purchasing':
                raise LabelInProgress()
            if label is not None and label['status'] == 'uncertain':
                raise LabelUncertain()
            if not can_transition(shipment['status'], 'label_pending'):
                raise InvalidTransition()
            token = uuid4()
            c.execute("INSERT INTO shipment_label(workspace_id,shipment_id,carrier,status,lease_token,lease_until) "
                      "SELECT workspace_id,id,carrier,'purchasing',%s,clock_timestamp()+%s*interval '1 second' FROM shipment WHERE id=%s "
                      "ON CONFLICT(workspace_id,shipment_id) DO UPDATE SET status='purchasing',attempts=shipment_label.attempts+1,"
                      "lease_token=EXCLUDED.lease_token,lease_until=EXCLUDED.lease_until,updated_at=clock_timestamp() "
                      "WHERE shipment_label.status='failed'", (token, lease_seconds, id))
            c.execute("UPDATE shipment SET status='label_pending',updated_at=clock_timestamp() WHERE id=%s", (id,))
            return token

    def finish_label(self, workspace, id, token, outcome, label=None):
        """Record the carrier outcome under an unexpired matching lease; False when the lease is gone."""
        if outcome not in ('purchased', 'rejected', 'unavailable', 'uncertain'):
            raise ValueError('Invalid outcome')
        if outcome == 'purchased' and not valid_label(label):
            outcome, label = 'uncertain', None
        label_status, shipment_status = {'purchased': ('purchased', 'labelled'), 'uncertain': ('uncertain', 'label_uncertain')}.get(
            outcome, ('failed', 'created'))
        with self.db.transaction(workspace) as c:
            done = c.execute(
                'UPDATE shipment_label SET status=%s,tracking_number=%s,label_ref=%s,lease_token=NULL,lease_until=NULL,'
                "updated_at=clock_timestamp() WHERE shipment_id=%s AND lease_token=%s AND status='purchasing' "
                'AND lease_until>clock_timestamp() RETURNING shipment_id',
                (label_status, label and label.tracking_number, label and label.label_ref, id, token)).fetchone()
            if done:
                c.execute("UPDATE shipment SET status=%s,updated_at=clock_timestamp() WHERE id=%s AND status='label_pending'",
                          (shipment_status, id))
            return bool(done)

    def purchase_label(self, workspace, id):
        """``(purchased|replayed, shipment)``, None for an unknown shipment.

        Raises LabelInProgress, LabelUncertain, LabelRejected or InvalidTransition."""
        shipment = self.get_shipment(workspace, id)
        if shipment is None:
            return None
        # Resolve the carrier before taking a lease: an unconfigured carrier was never called,
        # so it must be a plain rejection, not a quarantined uncertain label.
        carrier = self.carrier(shipment['carrier'])
        begun = self.begin_label(workspace, id)
        if begun is None or isinstance(begun, tuple):
            return begun
        shipment = self.get_shipment(workspace, id)
        label, outcome = None, 'uncertain'
        try:
            label = carrier.purchase_label(shipment, 'label/' + str(id))
            outcome = 'purchased'
        except CarrierError as exc:
            outcome = exc.outcome
        except Exception:  # noqa: BLE001 - any adapter failure is redacted and may have had an effect
            outcome = 'uncertain'
        try:
            finished = self.finish_label(workspace, id, begun, outcome, label)
        except Exception:  # noqa: BLE001 - e.g. a tracking number already used; the lease expires into uncertain
            finished = False
        current = self.get_shipment(workspace, id)
        if finished and current['label']['status'] == 'purchased':
            return 'purchased', current
        if finished and outcome in ('rejected', 'unavailable'):
            raise LabelRejected(outcome)
        raise LabelUncertain()

    def reconcile_label(self, workspace, id):
        """Ask the carrier about an uncertain label: ``(purchased|failed, shipment)``, None when unknown.

        A label that is not uncertain is returned unchanged; a lookup that fails stays uncertain."""
        with self.db.transaction(workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            if c.execute('SELECT 1 FROM shipment WHERE id=%s FOR UPDATE', (id,)).fetchone() is None:
                return None
            self._expire_leases(c, id)
            row = c.execute(f'{SELECT_SHIPMENT} WHERE s.id=%s', (id,)).fetchone()
        shipment = public_shipment(row)
        if shipment['label'] is None:
            raise InvalidTransition()
        if shipment['label']['status'] == 'purchasing':
            raise LabelInProgress()
        if shipment['label']['status'] != 'uncertain':
            return shipment['label']['status'], shipment
        carrier = self.carrier(shipment['carrier'])  # unconfigured: a rejection, the label stays uncertain
        try:
            found = carrier.find_label('label/' + str(id))
        except Exception:  # noqa: BLE001 - an unanswered lookup settles nothing
            raise LabelUncertain() from None
        if found is not None and not valid_label(found):
            raise LabelUncertain()
        status, target = ('purchased', 'labelled') if found else ('failed', 'created')
        try:
            with self.db.transaction(workspace) as c:
                if c.execute("UPDATE shipment_label SET status=%s,tracking_number=%s,label_ref=%s,updated_at=clock_timestamp() "
                             "WHERE shipment_id=%s AND status='uncertain' RETURNING shipment_id",
                             (status, found and found.tracking_number, found and found.label_ref, id)).fetchone():
                    c.execute("UPDATE shipment SET status=%s,updated_at=clock_timestamp() WHERE id=%s AND status='label_uncertain'",
                              (target, id))
        except psycopg.errors.UniqueViolation:
            # The carrier reported a tracking number another shipment holds: settle nothing.
            raise LabelUncertain() from None
        current = self.get_shipment(workspace, id)
        return current['label']['status'], current

    def validate_event(self, event):
        if not isinstance(event, dict) or set(event) != EVENT_FIELDS:
            raise ValueError('Invalid event')
        self.carrier(event['carrier'])
        if not valid_key(event['carrier_event_id']):
            raise ValueError('Invalid event')
        if not isinstance(event['tracking_number'], str) or not TRACKING_NUMBER.fullmatch(event['tracking_number']):
            raise ValueError('Invalid event')
        tracking_target('labelled', event['status'])  # raises for a status carriers cannot report
        at = event['occurred_at']
        if not isinstance(at, datetime) or at.tzinfo is None:
            raise ValueError('Invalid event')
        try:
            at = at.astimezone(UTC)
        except OverflowError:
            raise ValueError('Invalid event') from None
        if at < MIN_EVENT_TIME or at > datetime.now(UTC) + MAX_EVENT_SKEW:
            raise ValueError('Invalid event')
        return fingerprint([event['tracking_number'], event['status'], at.isoformat()])

    def record_tracking_event(self, workspace, event):
        """``(applied|recorded|duplicate, shipment)``; None when no purchased label has the tracking number.

        Events are deduplicated by (carrier, carrier_event_id). A new event changes the shipment only
        when it is not older than the last applied event and the move is allowed; otherwise it is
        kept as ``recorded``. Reusing an event id for different content is Conflict."""
        expected = self.validate_event(event)
        with self.db.transaction(workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            target = c.execute("SELECT shipment_id FROM shipment_label WHERE carrier=%s AND tracking_number=%s AND status='purchased'",
                               (event['carrier'], event['tracking_number'])).fetchone()
            if target is None:
                return None
            id = target['shipment_id']
            # One event at a time per shipment, so ordering and duplicates are decided serially.
            shipment = c.execute('SELECT status,last_event_at FROM shipment WHERE id=%s FOR UPDATE', (id,)).fetchone()
            stale = shipment['last_event_at'] is not None and event['occurred_at'] < shipment['last_event_at']
            status = None if stale else tracking_target(shipment['status'], event['status'])
            inserted = c.execute(
                'INSERT INTO tracking_event(workspace_id,carrier,carrier_event_id,shipment_id,status,occurred_at,fingerprint,outcome) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(workspace_id,carrier,carrier_event_id) DO NOTHING RETURNING outcome',
                (workspace, event['carrier'], event['carrier_event_id'], id, event['status'], event['occurred_at'], expected,
                 'recorded' if status is None else 'applied')).fetchone()
            if inserted is None:
                existing = c.execute('SELECT fingerprint FROM tracking_event WHERE carrier=%s AND carrier_event_id=%s',
                                     (event['carrier'], event['carrier_event_id'])).fetchone()
                if existing['fingerprint'] != expected:
                    raise Conflict()
                outcome = 'duplicate'
            else:
                outcome = inserted['outcome']
            if outcome == 'applied':
                c.execute('UPDATE shipment SET status=%s,last_event_at=%s,updated_at=clock_timestamp() WHERE id=%s',
                          (status, event['occurred_at'], id))
            return outcome, public_shipment(c.execute(f'{SELECT_SHIPMENT} WHERE s.id=%s', (id,)).fetchone())

    def list_tracking_events(self, workspace, id):
        with self.db.transaction(workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            if c.execute('SELECT 1 FROM shipment WHERE id=%s', (id,)).fetchone() is None:
                return None
            return c.execute(f'SELECT {EVENT_COLUMNS} FROM tracking_event WHERE shipment_id=%s '
                             'ORDER BY occurred_at,carrier_event_id LIMIT 500', (id,)).fetchall()

    def ready(self):
        with self.db.transaction(UUID(int=0)) as c:
            # Check every operation individually: a comma privilege list means ANY.
            granted = c.execute("""SELECT has_schema_privilege(current_user,'public','USAGE')
                AND has_table_privilege(current_user,'public.platform_schema_migrations','SELECT')
                AND has_table_privilege(current_user,'public.shipment','SELECT')
                AND has_table_privilege(current_user,'public.shipment','INSERT')
                AND has_table_privilege(current_user,'public.shipment','UPDATE')
                AND has_table_privilege(current_user,'public.shipment_label','SELECT')
                AND has_table_privilege(current_user,'public.shipment_label','INSERT')
                AND has_table_privilege(current_user,'public.shipment_label','UPDATE')
                AND has_table_privilege(current_user,'public.tracking_event','SELECT')
                AND has_table_privilege(current_user,'public.tracking_event','INSERT')""").fetchone()[0]
            if not granted:
                return False
            history = c.execute('SELECT version,checksum FROM platform_schema_migrations WHERE namespace=%s ORDER BY version',
                                ('logistics',)).fetchall()
            if history != [(m.version, m.checksum) for m in MIGRATIONS]:
                return False
            rls = c.execute('SELECT count(*)=%s AND bool_and(relrowsecurity AND relforcerowsecurity) FROM pg_class '
                            'WHERE oid = ANY(%s::regclass[])', (len(TABLES), list(TABLES))).fetchone()
            c.execute('SELECT status,fingerprint,last_event_at FROM shipment LIMIT 0')
            c.execute('SELECT status,lease_token,lease_until,tracking_number FROM shipment_label LIMIT 0')
            c.execute('SELECT fingerprint,outcome FROM tracking_event LIMIT 0')
            return bool(rls and rls[0])
