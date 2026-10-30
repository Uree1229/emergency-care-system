"""API-only team preflight and NEW synthetic morning cases; standard library only.

check sends GETs only. seed never edits a database, refreshes resource timestamps,
answers a hospital inquiry, confirms a destination or retries an uncertain POST.
The fixed tool actor is context metadata, not the existing API's audit actor.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import base64
import hashlib
from http.client import HTTPException
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener)


ACTOR = 'TEAM-READINESS'
BASELINE = 'server-allocation-baseline-20261030-v1'
CATALOG = Path(__file__).resolve().parents[1] / 'config/treatment_requirements.json'
HOSPITALS = {f'H{number:02d}' for number in range(1, 11)}
ENDPOINTS = ('/api/team-runtime', '/api/system/status', '/api/resource-model-scope',
             '/api/data-provenance', '/api/voice/capabilities')
PRIVATE_NETS = tuple(ipaddress.ip_network(value) for value in
                     ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
MAX_RESPONSE = 2 * 1024 * 1024
_SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
_SAFE_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z')


class ReadinessError(ValueError):
    """Only fixed diagnostic codes, never remote bodies or secret values."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _number(value, maximum):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or not 0 < value <= maximum):
        raise ReadinessError('INVALID_TIME_LIMIT')
    return value


def normalize_origin(value):
    if (not isinstance(value, str) or not value or len(value) > 2048 or
            any(ord(char) <= 32 or ord(char) >= 127 for char in value) or '\\' in value):
        raise ReadinessError('INVALID_SERVER_ORIGIN')
    try:
        parsed = urlsplit(value)
        port, host = parsed.port, parsed.hostname
        if (parsed.scheme not in ('http', 'https') or not host or
                parsed.username is not None or parsed.password is not None or
                parsed.path not in ('', '/') or parsed.query or parsed.fragment or
                (port is not None and not 1 <= port <= 65535)):
            raise ValueError
        if host == 'localhost':
            loopback = True
        else:
            address = ipaddress.ip_address(host)
            loopback = address.is_loopback
            if not loopback and not (address.version == 4 and any(address in net for net in PRIVATE_NETS)):
                raise ValueError
        if parsed.scheme == 'http' and not loopback:
            raise ReadinessError('PRIVATE_ORIGIN_REQUIRES_HTTPS')
    except ReadinessError:
        raise
    except (ValueError, TypeError):
        raise ReadinessError('INVALID_SERVER_ORIGIN') from None
    authority = '[' + host + ']' if ':' in host else host
    return parsed.scheme + '://' + authority + (':' + str(port) if port is not None else '')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class APIClient:
    def __init__(self, server, *, ca=None, token_file=None, timeout=5):
        self.origin = normalize_origin(server)
        self.timeout = _number(timeout, 30)
        self.authorization = None
        try:
            context = ssl.create_default_context(cafile=str(ca) if ca else None)
            if token_file is not None:
                path = Path(token_file)
                if path.stat().st_size > 512:
                    raise ReadinessError('INVALID_TOKEN_FILE')
                token = path.read_text(encoding='ascii').strip()
                if re.fullmatch(r'[A-Za-z0-9_-]{16,128}', token) is None:
                    raise ReadinessError('INVALID_TOKEN_FILE')
                self.authorization = 'Basic ' + base64.b64encode(('team:' + token).encode('ascii')).decode('ascii')
        except ReadinessError:
            raise
        except (OSError, UnicodeError, ssl.SSLError):
            raise ReadinessError('INVALID_TLS_OR_TOKEN_FILE') from None
        # Do not send LAN credentials via ambient proxies or follow redirects.
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())

    def request(self, method, path, body=None, *, missing_ok=False, timeout=None):
        if not path.startswith('/api/') or '?' in path or '#' in path:
            raise ReadinessError('INVALID_API_PATH')
        headers = {'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if self.authorization:
            headers['Authorization'] = self.authorization
        payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8') if body is not None else None
        request = Request(self.origin + path, data=payload, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=min(self.timeout, timeout) if timeout is not None else self.timeout) as response:
                if response.status != 200:
                    raise ReadinessError('UNEXPECTED_HTTP_STATUS')
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            if missing_ok and status == 404:
                return None
            code = ('AUTHENTICATION_REQUIRED' if status in (401, 403) else
                    'REDIRECT_REJECTED' if 300 <= status < 400 else
                    'PATIENT_ID_CONFLICT' if status == 409 else 'HTTP_REQUEST_FAILED')
            raise ReadinessError(code) from None
        except (URLError, OSError, TimeoutError, ssl.SSLError, HTTPException):
            raise ReadinessError('SERVER_CONNECTION_FAILED') from None
        if len(raw) > MAX_RESPONSE:
            raise ReadinessError('RESPONSE_TOO_LARGE')
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ReadinessError('INVALID_JSON_RESPONSE') from None
        if not isinstance(result, dict):
            raise ReadinessError('INVALID_JSON_RESPONSE')
        return result


def _catalog():
    try:
        raw = CATALOG.read_bytes()
        value = json.loads(raw)
        profiles = {row['id']: row for row in value['profiles']}
        for name in ('TR_GENERAL_ED', 'TR_CARDIAC_INTERVENTION'):
            if name not in profiles:
                raise ValueError
        return profiles, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        raise ReadinessError('LOCAL_CATALOG_UNAVAILABLE') from None


def _worker_ready(value):
    return (isinstance(value, dict) and value.get('state') == 'RUNNING' and
            value.get('enabled') is True and value.get('thread_alive') is True)


def _version(value):
    return value if isinstance(value, str) and _SAFE_VERSION.fullmatch(value) else None


def preflight(client):
    """Five read-only APIs; no patient GET (which can expire old acceptances)."""
    observed = int(time.time())
    try:
        runtime, health, scope, provenance, voice = [client.request('GET', path) for path in ENDPOINTS]
        _, catalog_sha = _catalog()
        database = runtime.get('database', {})
        resources = database.get('resources', {})
        runtime_workers, workers = runtime.get('workers', {}), health.get('workers', {})
        hospitals = provenance.get('hospitals', [])
        operating = [resource for hospital in hospitals for resource in hospital.get('operating_resources', [])]
        checks = {
            'common_full_runtime': runtime.get('revision') == 'team-runtime-20261030-v1' and
                runtime.get('mode') == 'full' and runtime.get('synthetic') is True and
                runtime.get('baseline_id') == BASELINE and runtime.get('inquiry_policy') == 'fixed_3' and
                runtime.get('solver_seconds') == 3 and runtime.get('external_hospital_messages') is False and
                runtime.get('travel_provider') == 'synthetic' and runtime.get('profile', {}).get('ok') is True,
            'workers_running': all(_worker_ready(group.get(name))
                for group in (runtime_workers, workers) for name in ('planning', 'inquiry')),
            'readable_current_status': health.get('status') == 'OK' and health.get('scope') == 'CURRENT_PROCESS' and
                health.get('database', {}).get('readable') is True and
                type(health.get('checked_at')) is int and abs(observed - health['checked_at']) <= 120,
            'common_synthetic_catalog': database.get('valid') is True and database.get('exists') is True and
                database.get('hospital_count') == 10 and len(hospitals) == 10 and
                {item.get('hospital_id') for item in hospitals} == HOSPITALS and
                all(item.get('identity', {}).get('origin', {}).get('classification') == 'synthetic_identity'
                    for item in hospitals),
            'fresh_operating_resources': resources.get('count') == 20 and resources.get('stale_count') == 0 and
                health.get('data', {}).get('stale_operating_resources') == 0 and len(operating) == 20 and
                all({resource.get('resource_type') for resource in item.get('operating_resources', [])}
                    == {'ed_bed', 'special_team'} for item in hospitals) and
                all(resource.get('update_freshness', {}).get('time_status') == 'recent' and
                    resource.get('update_freshness', {}).get('stale') is False and
                    resource.get('origin', {}).get('classification') in ('synthetic_fixture', 'manual_demo_entry')
                    for resource in operating),
            'catalog_and_resource_scope': scope.get('model_resources') == ['ed_bed', 'special_team'] and
                scope.get('public_observation_to_operating_capacity') is False and
                scope.get('public_observation_confirms_patient_acceptance') is False and
                scope.get('catalog', {}).get('sha256') == catalog_sha,
            'read_only_provenance': provenance.get('read_only') is True and provenance.get('external_api_calls') == 0,
            'voice_api_available': voice.get('schema_version') == '1' and voice.get('transcript_input') is True and
                voice.get('automatic_diagnosis') is False and _version(voice.get('voice_revision')) is not None,
        }
        failures = [name for name, ok in checks.items() if not ok]
        return dict(ready=not failures, status='READY' if not failures else 'NOT_READY',
            origin=client.origin, checked_at=observed, checks=checks, failed_checks=failures,
            versions=dict(runtime=_version(runtime.get('revision')), baseline=_version(runtime.get('baseline_id')),
                          voice=_version(voice.get('voice_revision')), commands=_version(voice.get('command_revision'))),
            speech_configured=voice.get('speech', {}).get('configured') is True)
    except ReadinessError as error:
        return dict(ready=False, status='NOT_READY', origin=client.origin, checked_at=observed, error_code=error.code)
    except (AttributeError, TypeError, KeyError, ValueError):
        return dict(ready=False, status='NOT_READY', origin=client.origin, checked_at=observed,
                    error_code='INVALID_PREFLIGHT_RESPONSE')


def check_server(server, *, ca=None, token_file=None, timeout=5):
    return preflight(APIClient(server, ca=ca, token_file=token_file, timeout=timeout))


@contextmanager
def _receipt(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = path.open('x', encoding='utf-8', newline='\n')
    except FileExistsError:
        raise ReadinessError('OUTPUT_ALREADY_EXISTS') from None
    try:
        def save(value):
            stream.seek(0)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        yield save
    finally:
        stream.close()


def _payload(pid, profile, run_id):
    cardiac = profile['id'] == 'TR_CARDIAC_INTERVENTION'
    demands = {}
    for group in ('resource_demands', 'extended_resource_demands'):
        for key, value in profile.get(group, {}).items():
            demands[key] = max(demands.get(key, 0), value)
    return dict(id=pid, reporter_role='transporter', consent=True, age=47,
        symptoms='[SYNTHETIC] ' + profile['label'], consciousness='alert', breathing='normal',
        priority=4, priority_source='expert', lat=35.1595, lon=126.8526,
        required_capabilities=list(profile['required_capabilities']), resource_demands=demands,
        auto_inquiry=True, inquiry_policy='fixed_3',
        extra=dict(treatment_profile_id=profile['id'], chief_complaint='cardiac' if cardiac else 'other',
            avpu='A', age_group='adult', pre_ktas_level=2, stemi_suspected='yes' if cardiac else 'no',
            vitals={'hr': 90}, synthetic_fixture=True, readiness_run_id=run_id, readiness_actor_id=ACTOR))


def _observe(entry, patient):
    pid = patient.get('id')
    if not isinstance(pid, str) or _SAFE_ID.fullmatch(pid) is None:
        raise ReadinessError('INVALID_RETURNED_PATIENT_ID')
    entry.update(patient_id=pid, state=patient.get('state') if patient.get('state') in
        ('WAITING', 'CONFIRMED', 'IN_TRANSIT', 'ARRIVED', 'CLOSED', 'CANCELLED') else 'UNKNOWN',
        observed_at=int(time.time()))
    if type(patient.get('requested_at')) is int:
        entry['requested_at'] = patient['requested_at']
    workflow = patient.get('inquiry_workflow', {})
    states = {'QUEUED', 'RUNNING', 'AWAITING_CONFIRMATION', 'EXHAUSTED', 'PAUSED', 'COMPLETED', 'ERROR', 'NOT_STARTED'}
    entry['workflow_state'] = workflow.get('state') if workflow.get('state') in states else 'UNKNOWN'
    entry['inquiry_state_counts'] = dict(Counter(q.get('state') if q.get('state') in
        ('PENDING', 'CHECKING', 'ACCEPTED', 'DECLINED', 'EXPIRED', 'CANCELLED', 'CONFIRMED') else 'UNKNOWN'
        for q in patient.get('inquiries', [])))
    if pid != entry['requested_patient_id']:
        raise ReadinessError('RETURNED_PATIENT_ID_MISMATCH')
    return workflow


def seed_case(server, case, run_id, output, *, ca=None, token_file=None, timeout=5, wait_seconds=15):
    """Create once through POST /api/patients; retain a receipt on partial failure."""
    if case not in ('general', 'cardiac', 'simultaneous'):
        raise ReadinessError('INVALID_CASE')
    if not isinstance(run_id, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}', run_id) is None:
        raise ReadinessError('INVALID_RUN_ID')
    _number(wait_seconds, 30)
    client = APIClient(server, ca=ca, token_file=token_file, timeout=timeout)
    profiles, _ = _catalog()
    names = ('TR_GENERAL_ED', 'TR_CARDIAC_INTERVENTION') if case == 'simultaneous' else (
        'TR_CARDIAC_INTERVENTION' if case == 'cardiac' else 'TR_GENERAL_ED',)
    entries = [dict(requested_patient_id='TEAM-' + run_id + '-' + suffix, patient_id=None,
                    state='NOT_SUBMITTED', submission_state='NOT_SUBMITTED', observed_at=int(time.time()))
               for suffix in ('A', 'B')[:len(names)]]
    receipt = dict(schema_version=1, synthetic=True, origin=client.origin, actor_id=ACTOR,
        actor_scope='tool_context_not_server_audit_actor', case=case, run_id=run_id,
        started_at=int(time.time()), finished_at=None, status='STARTED', patients=entries)
    with _receipt(output) as save:
        save(receipt)
        try:
            ready = preflight(client)
            if not ready['ready']:
                receipt['failed_checks'] = ready.get('failed_checks', [])
                raise ReadinessError(ready.get('error_code', 'PREFLIGHT_NOT_READY'))
            # Check the whole case before POSTing either patient. A concurrent
            # create is still safely rejected by the existing server's 409 guard.
            for entry in entries:
                existing = client.request('GET', '/api/patients/' + entry['requested_patient_id'], missing_ok=True)
                if existing is not None:
                    _observe(entry, existing)
                    entry['submission_state'] = 'EXISTING_NOT_CHANGED'
                    raise ReadinessError('PATIENT_ALREADY_EXISTS')
            for entry, name in zip(entries, names):
                entry['submission_state'] = 'POST_OUTCOME_UNKNOWN'
                save(receipt)  # Durable before the potentially committing request.
                patient = client.request('POST', '/api/patients', _payload(entry['requested_patient_id'], profiles[name], run_id))
                _observe(entry, patient)
                entry['submission_state'] = 'CREATED'
                save(receipt)
            # Both simultaneous cases are created before waiting. This is not
            # a promise of an identical server timestamp or three eligible sites.
            deadline = time.monotonic() + wait_seconds
            waiting = list(entries)
            while waiting:
                for entry in list(waiting):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ReadinessError('INITIAL_BATCH_TIMEOUT')
                    patient = client.request('GET', '/api/patients/' + entry['patient_id'], timeout=remaining)
                    workflow = _observe(entry, patient)
                    save(receipt)
                    if entry['state'] != 'WAITING':
                        raise ReadinessError('PATIENT_ADVANCED_EXTERNALLY')
                    if workflow.get('enabled') is True and workflow.get('policy', {}).get('name') == 'fixed_3' and (
                            workflow.get('round_no', 0) >= 1 and len(patient.get('inquiries', [])) >= 1):
                        waiting.remove(entry)
                    elif workflow.get('state') == 'ERROR':
                        raise ReadinessError('INQUIRY_WORKFLOW_ERROR')
                    elif workflow.get('state') in ('EXHAUSTED', 'PAUSED', 'COMPLETED'):
                        raise ReadinessError('NO_INITIAL_INQUIRY_AVAILABLE')
                if waiting:
                    time.sleep(min(.1, max(0, deadline - time.monotonic())))
            receipt['status'] = 'READY_FOR_MANUAL_RESPONSE'
        except ReadinessError as error:
            receipt.update(status='PARTIAL_OR_UNCERTAIN' if any(entry['submission_state'] in
                ('CREATED', 'POST_OUTCOME_UNKNOWN') for entry in entries) else 'NOT_CREATED', error_code=error.code)
        except (ValueError, TypeError, KeyError, AttributeError):
            receipt.update(status='PARTIAL_OR_UNCERTAIN', error_code='INVALID_CASE_RESPONSE')
        finally:
            receipt['finished_at'] = int(time.time())
            save(receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check')
    seed = commands.add_parser('seed')
    for command in (check, seed):
        command.add_argument('--server', required=True)
        command.add_argument('--ca', help='Trusted PEM CA certificate; TLS verification stays enabled')
        command.add_argument('--token-file', help='File containing the private gateway token only')
        command.add_argument('--timeout', type=float, default=5)
    seed.add_argument('--case', choices=('general', 'cardiac', 'simultaneous'), required=True)
    seed.add_argument('--run-id', required=True)
    seed.add_argument('--output', required=True, help='NEW receipt JSON; never overwrite')
    seed.add_argument('--wait-seconds', type=float, default=15)
    args = parser.parse_args(argv)
    options = dict(ca=args.ca, token_file=args.token_file, timeout=args.timeout)
    try:
        result = (check_server(args.server, **options) if args.command == 'check' else
                  seed_case(args.server, args.case, args.run_id, args.output, wait_seconds=args.wait_seconds, **options))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result.get('ready') or result.get('status') == 'READY_FOR_MANUAL_RESPONSE' else 1
    except ReadinessError as error:
        print(json.dumps(dict(status='FAILED', error_code=error.code)))
        return 1
    except OSError:
        print(json.dumps(dict(status='FAILED', error_code='RECEIPT_IO_FAILED')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
