from typing import Literal
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from platform_backend_core import create_app as base_app
from platform_backend_core import require_scope
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .carriers import configured_carriers
from .store import (
    Conflict,
    InvalidTransition,
    LabelInProgress,
    LabelRejected,
    LabelUncertain,
    LogisticsStore,
)


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Address(Strict):
    country: str = Field(min_length=2, max_length=2)
    postal_code: str = Field(min_length=2, max_length=12)


class Parcel(Strict):
    weight_grams: int
    length_mm: int
    width_mm: int
    height_mm: int


class RateRequest(Strict):
    origin: Address
    destination: Address
    parcel: Parcel


class ShipmentRequest(RateRequest):
    reference: str = Field(min_length=1, max_length=64)
    recipient_ref: str = Field(min_length=1, max_length=128)
    carrier: str = Field(max_length=32)
    service: str = Field(max_length=32)


class TrackingEvent(Strict):
    # FastAPI validates parsed JSON in Python mode, where a strict datetime refuses ISO strings;
    # only occurred_at is lax, and it must still carry a time zone.
    model_config = ConfigDict(extra='forbid', strict=False)
    carrier: str = Field(max_length=32, strict=True)
    carrier_event_id: str = Field(min_length=1, max_length=128, strict=True)
    tracking_number: str = Field(min_length=1, max_length=128, strict=True)
    status: Literal['in_transit', 'out_for_delivery', 'delivered', 'exception', 'returned']
    occurred_at: AwareDatetime


def json(body, status=200):
    return JSONResponse(jsonable_encoder(body), status)


def create_app(database, verifier, *, carriers=None):
    app = base_app('logistics', database, verifier, max_body_bytes=16384)
    carriers = configured_carriers() if carriers is None else dict(carriers)
    store = LogisticsStore(database, carriers)
    app.state.carriers = carriers
    app.state.readiness_checks['logistics_schema'] = store.ready
    app.state.readiness_checks['carriers'] = lambda: any(c.configured for c in carriers.values())

    # Every error is generic: no request values, carrier responses or addresses are echoed.
    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return json({'detail': 'Invalid request'}, 422)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return json({'detail': 'Idempotency conflict'}, 409)

    @app.exception_handler(InvalidTransition)
    async def transition(request, exc):
        return json({'detail': 'Invalid status transition'}, 409)

    @app.exception_handler(LabelInProgress)
    async def in_progress(request, exc):
        return json({'detail': 'Label purchase in progress'}, 409)

    @app.exception_handler(LabelUncertain)
    async def uncertain(request, exc):
        return json({'detail': 'Label outcome uncertain'}, 409)

    @app.exception_handler(LabelRejected)
    async def rejected(request, exc):
        if exc.outcome == 'unavailable':
            return json({'detail': 'Carrier unavailable'}, 503)
        return json({'detail': 'Label rejected'}, 422)

    def found(value):
        if value is None:
            raise HTTPException(404, 'Not found')
        return value

    @app.post('/v1/rates')
    def rates(body: RateRequest, p=Depends(require_scope('rates:read'))):
        data = body.model_dump()
        return {'rates': store.quote(data['origin'], data['destination'], data['parcel'])}

    @app.post('/v1/shipments')
    def create(body: ShipmentRequest, key: str = Header(alias='Idempotency-Key', max_length=128),
               p=Depends(require_scope('shipments:write'))):
        shipment, created = store.create_shipment(p.workspace, key, body.model_dump())
        return json(shipment, 201 if created else 200)

    @app.get('/v1/shipments')
    def listing(limit: int = Query(100, ge=1, le=100), after: UUID | None = None,
                p=Depends(require_scope('shipments:read'))):
        return store.list_shipments(p.workspace, limit, after)

    @app.get('/v1/shipments/{id}')
    def get(id: UUID, p=Depends(require_scope('shipments:read'))):
        return found(store.get_shipment(p.workspace, id))

    @app.post('/v1/shipments/{id}/cancel')
    def cancel(id: UUID, p=Depends(require_scope('shipments:write'))):
        return found(store.cancel(p.workspace, id))

    @app.post('/v1/shipments/{id}/label')
    def label(id: UUID, p=Depends(require_scope('labels:write'))):
        outcome, shipment = found(store.purchase_label(p.workspace, id))
        return json(shipment, 201 if outcome == 'purchased' else 200)

    @app.post('/v1/shipments/{id}/label/reconcile')
    def reconcile(id: UUID, p=Depends(require_scope('labels:write'))):
        return found(store.reconcile_label(p.workspace, id))[1]

    @app.get('/v1/shipments/{id}/events')
    def events(id: UUID, p=Depends(require_scope('shipments:read'))):
        return found(store.list_tracking_events(p.workspace, id))

    @app.post('/v1/tracking-events')
    def tracking(body: TrackingEvent, p=Depends(require_scope('tracking:write'))):
        outcome, shipment = found(store.record_tracking_event(p.workspace, body.model_dump()))
        return json({'outcome': outcome, 'shipment_id': shipment['id'], 'status': shipment['status']},
                    200 if outcome == 'duplicate' else 201)

    return app
