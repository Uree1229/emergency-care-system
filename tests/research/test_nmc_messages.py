"""실제 인증키·네트워크 없이 NMC 기관별 메시지 수집과 보존 계약을 확인한다."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

from server import db, nmc_messages as nm, service, sync_messages


NOW = 1_790_000_000


def message(hpid='A001', **changes):
    row = dict(hpid=hpid, emcOrgCod=hpid, rnum='1', dutyName='가상병원',
               symBlkMsg='장비 부족. 자세한 수용 조건은 확인 필요', symBlkMsgTyp='중증',
               symTypCod='Y0111', symTypCodMag='[산부인과응급] 분만',
               symOutDspYon='N', symOutDspMth='B',
               symBlkSttDtm='20261011090000', symBlkEndDtm='20261011130000')
    row.update(changes)
    return row


def xml(rows, total=None, page=1, code='00'):
    root = ET.Element('response')
    header = ET.SubElement(root, 'header')
    ET.SubElement(header, 'resultCode').text = code
    body = ET.SubElement(root, 'body')
    items = ET.SubElement(body, 'items')
    for row in rows:
        item = ET.SubElement(items, 'item')
        for name, value in row.items():
            if value is not None:
                ET.SubElement(item, name).text = value
    ET.SubElement(body, 'totalCount').text = str(len(rows) if total is None else total)
    if page is not None:
        ET.SubElement(body, 'pageNo').text = str(page)
    return ET.tostring(root, encoding='utf-8')


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return io.BytesIO(response)


class FakeClient:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def fetch_all(self, hpid):
        self.calls.append(hpid)
        result = self.results[hpid]
        if isinstance(result, BaseException):
            raise result
        return copy.deepcopy(result)


class MessageClientTests(unittest.TestCase):
    def test_exact_operation_hpid_query_and_encoded_key(self):
        opener = Opener(xml([message()]))
        client = nm.NmcMessageClient(key='FIXTURE%2BKEY%2F%3D', opener=opener)
        self.assertEqual(client.fetch_all('A001'), [message()])
        request, timeout = opener.calls[0]
        parsed = urllib.parse.urlparse(request.full_url)
        self.assertEqual(parsed.path, '/B552657/ErmctInfoInqireService/getEmrrmSrsillDissMsgInqire')
        self.assertEqual(urllib.parse.parse_qs(parsed.query),
                         {'ServiceKey': ['FIXTURE+KEY/='], 'HPID': ['A001'], 'pageNo': ['1'], 'numOfRows': ['100']})
        self.assertEqual(timeout, 15)

    def test_full_pagination_all_messages_same_hospital_allowed(self):
        second = message(rnum='2', symBlkMsg='다른 메시지')
        opener = Opener(xml([message()], total=2), xml([second], total=2, page=2))
        client = nm.NmcMessageClient(key='FIXTURE', opener=opener, page_size=1)
        self.assertEqual(client.fetch_all('A001'), [message(), second])
        self.assertEqual(len(opener.calls), 2)

    def test_empty_result_is_valid_complete_page(self):
        client = nm.NmcMessageClient(key='FIXTURE', opener=Opener(xml([])))
        self.assertEqual(client.fetch_all('A001'), [])

    def test_changed_total_wrong_page_and_truncated_page_fail(self):
        responses = [
            (xml([message()], total=2), xml([message(rnum='2')], total=3, page=2)),
            (xml([message()], total=2), xml([message(rnum='2')], total=2, page=1)),
            (xml([message()], total=2), xml([], total=2, page=2)),
        ]
        for pair in responses:
            with self.subTest(pair=pair):
                client = nm.NmcMessageClient(key='FIXTURE', opener=Opener(*pair))
                with self.assertRaises(nm.MessageDataError):
                    client.fetch_all('A001')

    def test_cross_hospital_or_mismatched_org_code_fails(self):
        for row in (message('A002'), message(emcOrgCod='A002'), message(hpid='')):
            with self.subTest(row=row):
                client = nm.NmcMessageClient(key='FIXTURE', opener=Opener(xml([row])))
                with self.assertRaises(nm.MessageDataError):
                    client.fetch_all('A001')

    def test_maximum_pages_never_returns_partial_result(self):
        client = nm.NmcMessageClient(key='FIXTURE', opener=Opener(xml([message()], total=2)), max_pages=1)
        with self.assertRaises(nm.MessageDataError):
            client.fetch_all('A001')

    def test_access_denied_does_not_retry_and_has_no_url_or_key(self):
        failure = urllib.error.HTTPError('https://example.invalid/?ServiceKey=FIXTURE', 403, 'forbidden', {}, None)
        opener = Opener(failure)
        client = nm.NmcMessageClient(key='FIXTURE', opener=opener)
        with self.assertRaises(nm.MessageDataError) as caught:
            client.fetch_all('A001')
        self.assertTrue(caught.exception.access_denied)
        self.assertNotIn('FIXTURE', str(caught.exception))
        self.assertNotIn('http', str(caught.exception))
        self.assertEqual(len(opener.calls), 1)

    def test_authentication_api_code_has_access_denied(self):
        opener = Opener(xml([], code='30'))
        client = nm.NmcMessageClient(key='FIXTURE', opener=opener)
        with self.assertRaises(nm.MessageDataError) as caught:
            client.fetch_all('A001')
        self.assertTrue(caught.exception.access_denied)
        self.assertEqual(len(opener.calls), 1)

    def test_network_error_is_safe_and_retryable(self):
        opener = Opener(urllib.error.URLError('https://example.invalid/?ServiceKey=FIXTURE'), xml([message()]))
        sleeps = []
        client = nm.NmcMessageClient(key='FIXTURE', opener=opener, sleep=sleeps.append)
        self.assertEqual(client.fetch_all('A001'), [message()])
        self.assertEqual(sleeps, [1])

    def test_credentials_scrubbed_from_even_successful_response(self):
        client = nm.NmcMessageClient(key='FIXTURE+KEY',
            opener=Opener(xml([message(symBlkMsg='FIXTURE+KEY FIXTURE%2BKEY')])) )
        serialized = json.dumps(client.fetch_all('A001'))
        self.assertNotIn('FIXTURE', serialized)

    def test_invalid_hpid_or_configuration_not_sent(self):
        opener = Opener()
        client = nm.NmcMessageClient(key='FIXTURE', opener=opener)
        for value in ('', 'A&HPID=A002', None, 123):
            with self.subTest(value=value), self.assertRaises(nm.MessageDataError):
                client.fetch_all(value)
        self.assertEqual(opener.calls, [])
        with self.assertRaises(nm.MessageDataError):
            nm.NmcMessageClient(key='FIXTURE', page_size=0)


class MessageNormalizerTests(unittest.TestCase):
    def test_exact_fields_states_and_windows_kept_distinct_from_observation(self):
        result = nm.normalize_messages([message()])
        item = result['messages'][0]
        self.assertEqual(item['display_state'], 'blocked')
        self.assertEqual(item['display_method'], 'manual')
        self.assertEqual(item['disease_code'], 'Y0111')
        self.assertIsInstance(item['starts_at'], int)
        self.assertLess(item['starts_at'], item['ends_at'])
        self.assertIsNone(item['source_observed_at'])
        self.assertIsNone(result['source_observed_at'])
        self.assertEqual(set(item['source_fields']), set(nm.MESSAGE_FIELDS))
        self.assertNotIn('accepted', item)
        self.assertNotIn('active', item)

    def test_official_korean_example_states_supported_without_text_inference(self):
        result = nm.normalize_messages([message(symOutDspYon='해제', symOutDspMth='수동', symBlkMsg='수용 불가')])
        item = result['messages'][0]
        self.assertEqual(item['display_state'], 'released')
        self.assertEqual(item['message'], '수용 불가')
        self.assertEqual(item['display_method'], 'manual')

    def test_unknown_status_dates_and_missing_message_never_become_no_restriction(self):
        result = nm.normalize_messages([message(symOutDspYon='N1', symOutDspMth='?',
                                               symBlkSttDtm='20260230090000', symBlkEndDtm='20261011', symBlkMsg='')])
        item = result['messages'][0]
        self.assertEqual(item['display_state'], 'unknown')
        self.assertEqual(item['display_method'], 'unknown')
        self.assertIsNone(item['starts_at'])
        self.assertIsNone(item['ends_at'])
        self.assertIsNone(item['message'])

    def test_empty_message_list_is_not_absence(self):
        result = nm.normalize_messages([])
        self.assertEqual(result['record_count'], 0)
        self.assertEqual(result['messages'], [])
        self.assertTrue(result['empty_result_not_absence'])


class MessageSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'fixture.db'
        self.conn = db.connect(str(self.path))
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def add_hospital(self, number, **changes):
        hospital = dict(id=f'NMC-A{number:03}', name=f'가상병원{number}', address='가상주소',
                        phone='000-000-0000', lat=37, lon=127, capabilities=['test'],
                        source='public', source_ids={'nmc': f'A{number:03}'}, capabilities_status='confirmed')
        hospital.update(changes)
        service.upsert_hospital(self.conn, hospital)

    def snapshot(self, hid='NMC-A001'):
        row = self.conn.execute('SELECT * FROM public_api_snapshots WHERE hospital_id=? AND endpoint=?',
                                (hid, nm.ENDPOINT)).fetchone()
        return dict(row) if row else None

    def test_complete_messages_and_empty_result_stored_without_operational_overwrite(self):
        self.add_hospital(1)
        self.add_hospital(2)
        service.set_resource(self.conn, NOW, 'NMC-A001', 'ed_bed', 10, 7)
        resources = [dict(row) for row in self.conn.execute('SELECT * FROM resources')]
        hospitals = [dict(row) for row in self.conn.execute('SELECT * FROM hospitals')]
        rows = [message(), message(rnum='2', symOutDspYon='Y')]
        report = sync_messages.sync_once(self.path, FakeClient({'A001': rows, 'A002': []}), lambda: NOW)
        self.assertEqual(report['status'], 'SUCCESS')
        self.assertEqual((report['requested_hospitals'], report['snapshots'], report['message_count'], report['empty_results']),
                         (2, 2, 2, 1))
        self.assertEqual(json.loads(self.snapshot()['raw_data']), {'items': rows})
        self.assertIsNone(self.snapshot()['source_observed_at'])
        self.assertEqual([dict(row) for row in self.conn.execute('SELECT * FROM resources')], resources)
        self.assertEqual([dict(row) for row in self.conn.execute('SELECT * FROM hospitals')], hospitals)
        view = service.hospital_view(self.conn, 'NMC-A001', NOW)
        observation = next(r for r in view['public_data'] if r['endpoint'] == nm.ENDPOINT)
        self.assertTrue(observation['observation_time_unknown'])

    def test_one_hospital_failure_keeps_its_previous_snapshot_other_complete_updates(self):
        self.add_hospital(1)
        self.add_hospital(2)
        nm.apply_bundles(self.conn, NOW, [{'hospital_id': 'NMC-A001', 'hpid': 'A001', 'records': [message()]}])
        old = self.snapshot()
        client = FakeClient({'A001': nm.MessageDataError('API 페이지 일부가 누락됐습니다'), 'A002': [message('A002')]})
        report = sync_messages.sync_once(self.path, client, lambda: NOW + 100)
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertEqual(report['snapshots'], 1)
        self.assertEqual(self.snapshot(), old)
        self.assertEqual(self.snapshot('NMC-A002')['fetched_at'], NOW + 100)

    def test_unexpected_error_secret_not_logged(self):
        self.add_hospital(1)
        report = sync_messages.sync_once(self.path, FakeClient({'A001': RuntimeError('https://secret.invalid?ServiceKey=FIXTURE')}), lambda: NOW)
        self.assertEqual(report['status'], 'ERROR')
        self.assertNotIn('FIXTURE', json.dumps(report))
        row = dict(self.conn.execute('SELECT * FROM nmc_message_sync_runs').fetchone())
        self.assertNotIn('secret.invalid', row['error'])

    def test_denied_stops_later_hospital_calls(self):
        self.add_hospital(1)
        self.add_hospital(2)
        client = FakeClient({'A001': nm.MessageDataError('API HTTP 오류: 403', access_denied=True), 'A002': []})
        report = sync_messages.sync_once(self.path, client, lambda: NOW)
        self.assertEqual(report['status'], 'ERROR')
        self.assertEqual(client.calls, ['A001'])
        self.assertEqual(report['snapshots'], 0)

    def test_cross_hospital_message_result_never_stored(self):
        self.add_hospital(1)
        report = sync_messages.sync_once(self.path, FakeClient({'A001': [message('A002')]}), lambda: NOW)
        self.assertEqual(report['status'], 'ERROR')
        self.assertIsNone(self.snapshot())

    def test_duplicate_provider_id_aborts_without_network_calls(self):
        self.add_hospital(1)
        self.add_hospital(2, source_ids={'nmc': 'A001'})
        client = FakeClient({})
        report = sync_messages.sync_once(self.path, client, lambda: NOW)
        self.assertEqual(report['status'], 'ERROR')
        self.assertEqual(client.calls, [])

    def test_source_id_missing_is_reported_not_inferred(self):
        self.add_hospital(1)
        self.add_hospital(2, source_ids={})
        report = sync_messages.sync_once(self.path, FakeClient({'A001': []}), lambda: NOW)
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertEqual(report['requested_hospitals'], 2)
        self.assertEqual(report['skipped_hospitals'], ['NMC-A002'])

    def test_changed_identity_or_invalid_bundle_rolls_back_other_hospital(self):
        self.add_hospital(1)
        self.add_hospital(2)
        bundles = [{'hospital_id': 'NMC-A001', 'hpid': 'A001', 'records': [message()]},
                   {'hospital_id': 'NMC-A002', 'hpid': 'A002', 'records': [message('A003')]}]
        with self.assertRaises(nm.MessageDataError):
            nm.apply_bundles(self.conn, NOW, bundles)
        self.assertIsNone(self.snapshot())

    def test_older_fetch_cannot_replace_newer_snapshot(self):
        self.add_hospital(1)
        bundle = {'hospital_id': 'NMC-A001', 'hpid': 'A001', 'records': [message()]}
        nm.apply_bundles(self.conn, NOW, [bundle])
        old = self.snapshot()
        bundle['records'] = []
        result = nm.apply_bundles(self.conn, NOW - 100, [bundle])
        self.assertEqual(result['skipped_older'], 1)
        self.assertEqual(self.snapshot(), old)

    def test_missing_db_fails_before_creating_client(self):
        with patch.object(sync_messages, 'NmcMessageClient') as client:
            with self.assertRaises(nm.MessageDataError):
                sync_messages.sync_once(self.path.parent / 'missing.db')
        client.assert_not_called()

    def test_cli_writes_safe_report_and_latest_pointer(self):
        self.add_hospital(1)
        report = sync_messages.sync_once(self.path, FakeClient({'A001': []}), lambda: NOW)
        output = self.path.parent / 'output'
        with patch.object(sync_messages, 'sync_once', return_value=report), patch('sys.stdout', new=io.StringIO()):
            code = sync_messages.main(['--db', str(self.path), '--out', str(output)])
        self.assertEqual(code, 0)
        pointer = json.loads((output / 'latest.json').read_text(encoding='utf-8'))
        saved = json.loads(Path(pointer['report']).read_text(encoding='utf-8'))
        self.assertEqual(saved, report)


if __name__ == '__main__':
    unittest.main()
