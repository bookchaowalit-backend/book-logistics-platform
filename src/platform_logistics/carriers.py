"""Carrier adapter interface and the in-process sandbox carrier (no network, no credentials)."""
import hashlib
import os
import threading
from dataclasses import dataclass
from typing import Protocol

from .rules import billable_weight_grams, validate_address, validate_parcel

OUTCOMES = ('rejected', 'unavailable', 'uncertain')


class CarrierError(Exception):
    """rejected/unavailable: the carrier did nothing. uncertain: it may have bought the label."""

    def __init__(self, outcome):
        self.outcome = outcome if outcome in OUTCOMES else 'uncertain'
        super().__init__('Carrier unavailable')


@dataclass(frozen=True, slots=True)
class Label:
    tracking_number: str
    label_ref: str


class Carrier(Protocol):
    name: str
    configured: bool

    def quote(self, origin: dict, destination: dict, parcel: dict) -> list[dict]: ...

    def purchase_label(self, shipment: dict, idempotency_key: str) -> Label: ...

    def find_label(self, idempotency_key: str) -> Label | None: ...


# service: (domestic base, domestic per extra step, international base, international per step, days dom/intl)
SANDBOX_TARIFF = {
    'standard': (4000, 1500, 45000, 12000, 3, 10),
    'express': (8000, 2500, 90000, 20000, 1, 4),
}


class SandboxCarrier:
    """Deterministic fixture carrier.

    Labels are keyed by the caller's idempotency key, like a real carrier's idempotent
    purchase API, and live only in this process. ``script`` lists outcomes for the next
    calls: ``rejected``, ``unavailable``, ``uncertain_before`` (fails before any effect),
    ``uncertain_after`` (buys the label, then loses the response) and ``lookup_uncertain``.
    """

    name = 'sandbox'
    configured = True

    def __init__(self, script=None):
        self._labels = {}
        self._script = list(script or [])
        self._lock = threading.Lock()
        self.purchases = 0

    def _next(self):
        with self._lock:
            return self._script.pop(0) if self._script else 'ok'

    def quote(self, origin, destination, parcel):
        validate_address(origin)
        validate_address(destination)
        validate_parcel(parcel)
        weight = billable_weight_grams(parcel)
        steps = weight // 500
        domestic = origin['country'] == destination['country']
        rates = []
        for service, (dom, dom_step, intl, intl_step, dom_days, intl_days) in SANDBOX_TARIFF.items():
            base, step = (dom, dom_step) if domestic else (intl, intl_step)
            rates.append({'carrier': self.name, 'service': service, 'amount_minor': base + (steps - 1) * step,
                          'currency': 'THB', 'billable_weight_grams': weight,
                          'estimated_days': dom_days if domestic else intl_days})
        return rates

    def _record(self, idempotency_key):
        with self._lock:
            label = self._labels.get(idempotency_key)
            if label is None:
                digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:12].upper()
                label = Label('SBX' + digest, 'sandbox://labels/SBX' + digest)
                self._labels[idempotency_key] = label
                self.purchases += 1
            return label

    def purchase_label(self, shipment, idempotency_key):
        step = self._next()
        if step in ('rejected', 'unavailable'):
            raise CarrierError(step)
        if step == 'uncertain_before':
            raise CarrierError('uncertain')
        label = self._record(idempotency_key)
        if step == 'uncertain_after':
            raise CarrierError('uncertain')
        return label

    def find_label(self, idempotency_key):
        if self._next() == 'lookup_uncertain':
            raise CarrierError('uncertain')
        with self._lock:
            return self._labels.get(idempotency_key)


def configured_carriers():
    """Carriers named in LOGISTICS_CARRIERS. Only the sandbox exists; unknown names are ignored."""
    names = [n.strip() for n in os.environ.get('LOGISTICS_CARRIERS', '').split(',') if n.strip()]
    return {'sandbox': SandboxCarrier()} if 'sandbox' in names else {}
