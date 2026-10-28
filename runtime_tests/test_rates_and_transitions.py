"""Pure rules: deterministic sandbox rates and validated fulfillment transitions."""
import pytest

from platform_logistics.carriers import (
    CarrierError,
    SandboxCarrier,
    configured_carriers,
)
from platform_logistics.rules import (
    STATUSES,
    TRACKING_STATUSES,
    billable_weight_grams,
    can_transition,
    tracking_target,
    validate_parcel,
)

DOMESTIC = {'origin': {'country': 'TH', 'postal_code': '10110'}, 'destination': {'country': 'TH', 'postal_code': '50000'}}
ABROAD = {'origin': {'country': 'TH', 'postal_code': '10110'}, 'destination': {'country': 'SG', 'postal_code': '018956'}}
SMALL = {'weight_grams': 400, 'length_mm': 200, 'width_mm': 150, 'height_mm': 50}


def test_billable_weight_uses_volumetric_and_rounds_up_to_half_kilo():
    assert billable_weight_grams(SMALL) == 500
    assert billable_weight_grams({'weight_grams': 501, 'length_mm': 10, 'width_mm': 10, 'height_mm': 10}) == 1000
    # 400x300x200 mm is 4800 g volumetric at a 5000 divisor.
    assert billable_weight_grams({'weight_grams': 1000, 'length_mm': 400, 'width_mm': 300, 'height_mm': 200}) == 5000


def test_sandbox_quote_is_deterministic_and_zone_priced():
    carrier = SandboxCarrier()
    first = carrier.quote(DOMESTIC['origin'], DOMESTIC['destination'], SMALL)
    assert first == carrier.quote(DOMESTIC['origin'], DOMESTIC['destination'], SMALL)
    assert [(r['service'], r['amount_minor'], r['currency'], r['estimated_days']) for r in first] == [
        ('standard', 4000, 'THB', 3), ('express', 8000, 'THB', 1)]
    heavy = carrier.quote(DOMESTIC['origin'], DOMESTIC['destination'], SMALL | {'weight_grams': 1200})
    assert [r['amount_minor'] for r in heavy] == [4000 + 2 * 1500, 8000 + 2 * 2500]
    abroad = carrier.quote(ABROAD['origin'], ABROAD['destination'], SMALL)
    assert [r['amount_minor'] for r in abroad] == [45000, 90000]
    assert all(r['carrier'] == 'sandbox' and r['billable_weight_grams'] == 500 for r in first + abroad)


@pytest.mark.parametrize('parcel', [
    SMALL | {'weight_grams': 0}, SMALL | {'weight_grams': 30001}, SMALL | {'length_mm': 1501},
    SMALL | {'height_mm': 0}, SMALL | {'weight_grams': 1.5}, SMALL | {'weight_grams': True},
])
def test_parcel_bounds_are_enforced(parcel):
    with pytest.raises(ValueError):
        validate_parcel(parcel)


def test_transitions_follow_the_fulfillment_graph():
    assert can_transition('created', 'label_pending') and can_transition('created', 'cancelled')
    assert can_transition('label_pending', 'labelled') and can_transition('label_pending', 'label_uncertain')
    assert can_transition('labelled', 'in_transit') and can_transition('in_transit', 'delivered')
    assert can_transition('out_for_delivery', 'in_transit') and can_transition('exception', 'returned')
    for terminal in ('delivered', 'returned', 'cancelled'):
        assert not any(can_transition(terminal, target) for target in STATUSES)
    assert not can_transition('labelled', 'cancelled')
    assert not can_transition('created', 'in_transit')
    assert not can_transition('label_uncertain', 'in_transit')
    assert not can_transition('created', 'unknown') and not can_transition('unknown', 'created')


def test_tracking_target_only_moves_forward_from_a_labelled_shipment():
    assert set(TRACKING_STATUSES) == {'in_transit', 'out_for_delivery', 'delivered', 'exception', 'returned'}
    assert tracking_target('labelled', 'in_transit') == 'in_transit'
    assert tracking_target('in_transit', 'in_transit') is None
    assert tracking_target('delivered', 'in_transit') is None
    assert tracking_target('created', 'in_transit') is None
    with pytest.raises(ValueError):
        tracking_target('labelled', 'label_pending')


def test_sandbox_label_is_idempotent_per_key_and_scriptable():
    carrier = SandboxCarrier()
    shipment = {'id': 'fixture'}
    first = carrier.purchase_label(shipment, 'label/one')
    assert carrier.purchase_label(shipment, 'label/one') == first and carrier.purchases == 1
    assert carrier.purchase_label(shipment, 'label/two') != first and carrier.purchases == 2
    assert first.tracking_number.startswith('SBX') and carrier.find_label('label/one') == first
    assert carrier.find_label('label/none') is None
    scripted = SandboxCarrier(script=['rejected', 'uncertain_after', 'uncertain_before'])
    for outcome, effect in (('rejected', 0), ('uncertain', 1), ('uncertain', 1)):
        with pytest.raises(CarrierError) as exc:
            scripted.purchase_label(shipment, 'label/scripted' if outcome != 'rejected' else 'label/rejected')
        assert exc.value.outcome == outcome and str(exc.value) == 'Carrier unavailable'
        assert scripted.purchases == effect
    assert scripted.find_label('label/rejected') is None and scripted.find_label('label/scripted') is not None


def test_carriers_are_opt_in_by_name(monkeypatch):
    monkeypatch.delenv('LOGISTICS_CARRIERS', raising=False)
    assert configured_carriers() == {}
    monkeypatch.setenv('LOGISTICS_CARRIERS', 'sandbox,unknown')
    assert list(configured_carriers()) == ['sandbox']
