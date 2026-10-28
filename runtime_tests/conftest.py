"""Disposable PostgreSQL databases with NOSUPERUSER NOBYPASSRLS runtime roles, and signed test tokens."""
import os
import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from platform_backend_core import Database, MigrationRunner, TokenVerifier
from platform_backend_core.testing import temporary_database

from platform_logistics.migrations import MIGRATIONS

SHIPMENT = {
    'reference': 'order-1001',
    'recipient_ref': 'contact:fixture-1',
    'carrier': 'sandbox',
    'service': 'standard',
    'origin': {'country': 'TH', 'postal_code': '10110'},
    'destination': {'country': 'TH', 'postal_code': '50000'},
    'parcel': {'weight_grams': 400, 'length_mm': 200, 'width_mm': 150, 'height_mm': 50},
}


def admin_dsn():
    dsn = os.environ.get('PLATFORM_TEST_DSN')
    if not dsn:
        pytest.skip('explicit disposable PostgreSQL DSN required')
    return dsn


@pytest.fixture
def temp_db():
    with temporary_database(admin_dsn(), prefix='logi_') as temp:
        yield temp


@pytest.fixture
def database(temp_db):
    MigrationRunner(temp_db.admin_dsn, namespace='logistics').apply(MIGRATIONS)
    temp_db.grant_schema('public')
    db = Database(temp_db.runtime_dsn)
    yield db
    db.close()


def auth():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    verifier = TokenVerifier(issuer='fixture', audience='fixture', public_key=public)

    def headers(scopes, workspace=None, key_header='fixture-key'):
        now = int(time.time())
        token = jwt.encode({'iss': 'fixture', 'aud': 'fixture', 'sub': str(uuid4()), 'ws': str(workspace or uuid4()),
                            'iat': now, 'exp': now + 300, 'jti': str(uuid4()), 'scope': scopes}, key, algorithm='RS256')
        result = {'Authorization': 'Bearer ' + token}
        if key_header:
            result['Idempotency-Key'] = key_header
        return result
    return verifier, headers
