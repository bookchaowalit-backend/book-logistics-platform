"""Pure fulfillment rules: parcel bounds, billable weight and the status graph."""
import re

from .errors import InvalidInput

STATUSES = ('created', 'label_pending', 'label_uncertain', 'labelled', 'in_transit',
            'out_for_delivery', 'exception', 'delivered', 'returned', 'cancelled')
TRACKING_STATUSES = ('in_transit', 'out_for_delivery', 'delivered', 'exception', 'returned')
# Every allowed move. delivered, returned and cancelled are terminal.
TRANSITIONS = {
    'created': {'label_pending', 'cancelled'},
    'label_pending': {'labelled', 'label_uncertain', 'created'},
    'label_uncertain': {'labelled', 'created'},
    'labelled': {'in_transit', 'out_for_delivery', 'delivered', 'exception'},
    'in_transit': {'out_for_delivery', 'delivered', 'exception', 'returned'},
    'out_for_delivery': {'in_transit', 'delivered', 'exception'},
    'exception': {'in_transit', 'out_for_delivery', 'delivered', 'returned'},
    'delivered': set(), 'returned': set(), 'cancelled': set(),
}
MAX_WEIGHT_GRAMS = 30000
MAX_SIDE_MM = 1500
VOLUMETRIC_DIVISOR = 5000  # mm^3 per gram, the common 5000 cm^3/kg factor
WEIGHT_STEP_GRAMS = 500
COUNTRY = re.compile(r'[A-Z]{2}')
POSTAL_CODE = re.compile(r'[A-Za-z0-9][A-Za-z0-9 -]{1,11}')


def _bounded_int(value, low, high):
    return type(value) is int and low <= value <= high


def validate_parcel(parcel):
    if not isinstance(parcel, dict) or set(parcel) != {'weight_grams', 'length_mm', 'width_mm', 'height_mm'}:
        raise InvalidInput('Invalid parcel')
    if not _bounded_int(parcel['weight_grams'], 1, MAX_WEIGHT_GRAMS):
        raise InvalidInput('Invalid parcel')
    if not all(_bounded_int(parcel[k], 1, MAX_SIDE_MM) for k in ('length_mm', 'width_mm', 'height_mm')):
        raise InvalidInput('Invalid parcel')
    return parcel


def validate_address(address):
    if not isinstance(address, dict) or set(address) != {'country', 'postal_code'}:
        raise InvalidInput('Invalid address')
    if not isinstance(address['country'], str) or not COUNTRY.fullmatch(address['country']):
        raise InvalidInput('Invalid address')
    if not isinstance(address['postal_code'], str) or not POSTAL_CODE.fullmatch(address['postal_code']):
        raise InvalidInput('Invalid address')
    return address


def billable_weight_grams(parcel):
    validate_parcel(parcel)
    volumetric = -(-parcel['length_mm'] * parcel['width_mm'] * parcel['height_mm'] // VOLUMETRIC_DIVISOR)
    weight = max(parcel['weight_grams'], volumetric)
    return -(-weight // WEIGHT_STEP_GRAMS) * WEIGHT_STEP_GRAMS


def can_transition(current, target):
    return target in TRANSITIONS.get(current, ())


def tracking_target(current, event_status):
    """New shipment status for a carrier event, or None when the event changes nothing."""
    if event_status not in TRACKING_STATUSES:
        raise InvalidInput('Invalid tracking status')
    return event_status if can_transition(current, event_status) else None
