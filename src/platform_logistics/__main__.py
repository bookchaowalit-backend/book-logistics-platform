"""Explicit service lifecycle; migrations never run at API startup."""
import argparse
import os

from platform_backend_core import Database, MigrationRunner, TokenVerifier

from .app import create_app
from .carriers import configured_carriers
from .migrations import MIGRATIONS


def main():
    parser = argparse.ArgumentParser(prog='platform_logistics')
    parser.add_argument('command', choices=['serve', 'migrate'])
    args = parser.parse_args()
    try:
        if args.command == 'migrate':
            MigrationRunner(os.environ['DATABASE_URL'], namespace='logistics').apply(MIGRATIONS)
            return
        carriers = configured_carriers()
        if not carriers:
            raise ValueError('No carriers')
        verifier = TokenVerifier(issuer=os.environ['JWT_ISSUER'], audience=os.environ['JWT_AUDIENCE'],
                                 public_key=os.environ.get('JWT_PUBLIC_KEY'), jwks_url=os.environ.get('JWT_JWKS_URL'))
        app = create_app(Database(os.environ['DATABASE_URL']), verifier, carriers=carriers)
        import uvicorn
        uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8080')), access_log=False, limit_concurrency=100)
    except Exception:  # noqa: BLE001 - startup errors are reported without configuration values
        raise SystemExit('Service configuration or dependency unavailable') from None


if __name__ == '__main__':
    main()
