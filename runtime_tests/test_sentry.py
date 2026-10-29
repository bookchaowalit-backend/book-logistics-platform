"""Sentry is off without SENTRY_DSN; with it: errors only, no PII, locals, bodies or secrets."""
import sys

import pytest
import sentry_sdk

import platform_logistics
from platform_logistics import _sentry


def _clear_env(monkeypatch):
    for name in ('SENTRY_DSN', 'SENTRY_ENVIRONMENT', 'SENTRY_RELEASE', 'RAILWAY_GIT_COMMIT_SHA', 'RAILWAY_SERVICE_NAME'):
        monkeypatch.delenv(name, raising=False)


def test_package_import_initialises_sentry():
    # The package __init__ wires init_sentry, so `python -m platform_logistics serve` reports errors.
    assert platform_logistics._init_sentry is _sentry.init_sentry


def test_no_dsn_is_a_noop(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('SENTRY_DSN', '   ')
    calls = []
    fake = type('Fake', (), {'init': staticmethod(lambda **kw: calls.append(kw))})
    monkeypatch.setitem(sys.modules, 'sentry_sdk', fake)
    _sentry.init_sentry()
    assert calls == []


def test_dsn_initialises_errors_only_without_pii(monkeypatch, capsys):
    _clear_env(monkeypatch)
    monkeypatch.setenv('SENTRY_DSN', 'https://public@o0.ingest.sentry.io/0')
    monkeypatch.setenv('RAILWAY_SERVICE_NAME', 'logistics-api')
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'abc123')
    calls, tags = [], {}
    monkeypatch.setattr(sentry_sdk, 'init', lambda **kw: calls.append(kw))
    monkeypatch.setattr(sentry_sdk, 'set_tag', lambda key, value: tags.__setitem__(key, value))
    _sentry.init_sentry()
    (kw,) = calls
    assert kw['environment'] == 'production'
    assert kw['release'] == 'abc123'
    assert kw['traces_sample_rate'] is None
    assert kw['send_default_pii'] is False
    assert kw['include_local_variables'] is False
    assert kw['max_request_body_size'] == 'never'
    assert kw['before_send'] is _sentry._scrub_event
    (logging_integration,) = kw['integrations']
    assert logging_integration._handler is None
    assert logging_integration._breadcrumb_handler is None
    assert logging_integration._sentry_logs_handler is None
    assert tags == {'service': 'logistics-api'}
    captured = capsys.readouterr()
    assert 'sentry: enabled for logistics-api' in captured.err
    assert captured.out == ''
    assert 'ingest.sentry.io' not in captured.err


def test_release_is_unset_without_a_git_sha(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('SENTRY_DSN', 'https://public@o0.ingest.sentry.io/0')
    calls = []
    monkeypatch.setattr(sentry_sdk, 'init', lambda **kw: calls.append(kw))
    monkeypatch.setattr(sentry_sdk, 'set_tag', lambda key, value: None)
    _sentry.init_sentry()
    assert calls[0]['release'] is None


def test_scrub_removes_url_passwords_tokens_jwts_emails_and_ips():
    text = (
        'connect postgresql://svc:'
        's3cr%40t'  # split so secret scanners do not read a synthetic URL as a credential
        '@ep-x.neon.tech/db failed; '
        'password=hunter2 Authorization: Bearer abc.def-ghi '
        'eyJhbGciOiJSUzI1NiJ9' '.' 'eyJzdWIiOiJvd25lciJ9' '.' 'c2lnbmF0dXJl'
        ' for buyer.one+tag@example.co.th from 203.0.113.77 and 2001:db8:85a3:0:0:8a2e:370:7334'
    )  # split: not a secret
    out = _sentry.scrub(text)
    for secret in ('s3cr%40t', 'hunter2', 'abc.def-ghi', 'eyJhbGciOiJSUzI1NiJ9', 'buyer.one', 'example.co.th', '203.0.113.77', '8a2e:370:7334'):
        assert secret not in out
    assert 'postgresql://svc:***@ep-x.neon.tech/db' in out


def test_scrub_event_drops_request_details_user_and_extra():
    event = {
        'exception': {'values': [{'value': 'bad url postgres://u:pw@h/db for a@b.example'}]},
        'message': 'contact a@b.example',
        'request': {
            'method': 'POST',
            'url': 'https://x/api?token=t',
            'data': {'address': '1 Main Rd'},
            'headers': {'authorization': 'Bearer t', 'x-forwarded-for': '198.51.100.1'},
            'cookies': {'session': 's'},
            'query_string': 'token=t',
            'env': {'REMOTE_ADDR': '198.51.100.1'},
        },
        'user': {'ip_address': '198.51.100.1', 'email': 'a@b.example'},
        'breadcrumbs': {'values': [{'message': 'GET with Bearer abc', 'data': {'url': 'https://x?token=t'}}]},
        'extra': {'sys.argv': ['--token', 't']},
    }
    out = _sentry._scrub_event(event, {})
    assert out['exception']['values'][0]['value'] == 'bad url postgres://u:***@h/db for ***email***'
    assert out['message'] == 'contact ***email***'
    assert out['request'] == {'method': 'POST'}
    assert 'user' not in out
    assert 'data' not in out['breadcrumbs']['values'][0]
    assert 'abc' not in out['breadcrumbs']['values'][0]['message']
    assert 'extra' not in out
    for value in ('198.51.100.1', 'a@b.example', '1 Main Rd', 'token=t'):
        assert value not in str(out)


def test_scrub_event_keeps_only_scrubbed_formatted_log_text():
    token = 'opaque' + '-token-value'
    event = {
        'logentry': {
            'message': 'Authorization: Bearer %s',
            'params': [token],
            'formatted': 'Authorization: Bearer ' + token,
        }
    }
    out = _sentry._scrub_event(event, {})
    assert out['logentry'] == {'message': 'Authorization: Bearer ***'}
    assert token not in str(out)


def test_scrub_event_drops_dynamic_sampling_context():
    event = {'contexts': {'trace': {'trace_id': 't', 'dynamic_sampling_context': {'user_id': 'alice'}}}}
    out = _sentry._scrub_event(event, {})
    assert out['contexts']['trace'] == {'trace_id': 't'}


@pytest.mark.parametrize('value', [None, 42])
def test_scrub_event_tolerates_missing_or_odd_fields(value):
    event = {'exception': {'values': [{'value': value}]}, 'request': 'x', 'logentry': None}
    out = _sentry._scrub_event(event, {})
    assert out['exception']['values'][0]['value'] == value
