"""Isolated SQLite voice dictionary and reference-import contracts."""
import copy
import csv
from pathlib import Path
import sqlite3
import tempfile
import unittest

from server import voice_lexicon as lexicon
from server.service import ServiceError


class VoiceLexiconTests(unittest.TestCase):
    def setUp(self):
        self.conn = self.new_connection()
        self.tmp = tempfile.TemporaryDirectory(prefix='voice-lexicon-csv-')

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    @staticmethod
    def new_connection():
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(lexicon.SCHEMA)
        lexicon.seed(conn)
        return conn

    def csv(self, name='terms.csv', rows=None, encoding='utf-8-sig'):
        path = Path(self.tmp.name)/name
        with path.open('w', encoding=encoding, newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['상병기호', '한글명', '영문명'])
            writer.writerows(rows if rows is not None else [
                ['A000', '콜레라', 'Cholera'], ['R079', '상세불명의 흉통', 'Chest pain, unspecified']])
        return path

    def load(self, path, version='20250930', **extra):
        args = dict(source_id='official.fixture', version=version,
            title='Synthetic official-format code fixture', url='https://example.invalid/reference',
            license='Fixture only', code_column='상병기호', korean_column='한글명', english_column='영문명')
        args.update(extra)
        return lexicon.import_reference_csv(self.conn, path, **args)

    def rows(self):
        return {table: [tuple(row) for row in self.conn.execute('SELECT * FROM '+table+' ORDER BY 1,2,3')]
                for table in ('voice_term_sources', 'voice_terms', 'voice_term_aliases')}

    def test_seed_is_idempotent_and_has_explicit_provenance(self):
        before = self.rows()
        report = lexicon.seed(self.conn)
        self.assertEqual(before, self.rows())
        self.assertEqual(report['storage'], 'sqlite')
        self.assertGreater(report['term_count'], 28)
        for source in report['sources']:
            self.assertTrue(source['version'])
            self.assertEqual(len(source['sha256']), 64)
            self.assertTrue(source['runtime_enabled'])
            self.assertIn('not clinically reviewed', source['license'])

    def test_reviewed_alias_reaches_runtime_snapshot_and_survives_seed(self):
        lexicon.add_alias(self.conn, lexicon.PROJECT_TERMS, 'symptom:흉통', '가슴 조이는 통증', 'fixture-reviewer')
        lexicon.seed(self.conn)
        item = next(item for item in lexicon.snapshot(self.conn)['catalog']['terms']['symptoms'] if item['canonical']=='흉통')
        self.assertIn('가슴 조이는 통증', item['synonyms'])
        row = self.conn.execute("SELECT origin,reviewed_by FROM voice_term_aliases WHERE alias='가슴 조이는 통증'").fetchone()
        self.assertEqual(tuple(row), ('reviewed', 'fixture-reviewer'))

    def test_custom_alias_does_not_leak_to_other_database_or_detached_snapshot(self):
        other = self.new_connection()
        try:
            lexicon.add_alias(self.conn, lexicon.PROJECT_TERMS, 'measurement:pulse', '새 맥박 표현', 'fixture-reviewer')
            first = lexicon.snapshot(self.conn)
            self.assertIn('새 맥박 표현', first['catalog']['terms']['measurements']['pulse']['synonyms'])
            self.assertNotIn('새 맥박 표현', lexicon.snapshot(other)['catalog']['terms']['measurements']['pulse']['synonyms'])
            first['catalog']['terms']['measurements']['pulse']['unit'] = 'tampered'
            self.assertEqual(lexicon.snapshot(self.conn)['catalog']['terms']['measurements']['pulse']['unit'], '회/분')
        finally:
            other.close()

    def test_same_alias_cannot_redirect_to_another_target(self):
        before = self.rows()
        with self.assertRaises(ServiceError) as error:
            lexicon.add_alias(self.conn, lexicon.PROJECT_TERMS, 'measurement:pulse', '혈당', 'fixture-reviewer')
        self.assertEqual(error.exception.code, 'VOICE_DICTIONARY_ALIAS_CONFLICT')
        self.assertEqual(before, self.rows())

    def test_schema_and_limits_cannot_be_changed_by_db_payload(self):
        self.conn.execute("UPDATE voice_terms SET payload=? WHERE code='measurement:pulse'", ('{"min":-999,"max":999999,"field":"priority"}',))
        self.conn.execute("INSERT INTO voice_terms(source_id,source_version,code,kind,label,payload) "
            "SELECT source_id,version,'measurement:priority','measurement','tampered','{}' FROM voice_term_sources WHERE source_id=? AND active=1",
            (lexicon.PROJECT_TERMS,))
        actual = lexicon.snapshot(self.conn)['catalog']
        self.assertNotIn('priority', actual['terms']['measurements'])
        self.assertEqual(actual['terms']['measurements']['pulse']['min'], 0)

    def test_r1_aliases_are_db_backed_without_changing_target_sets(self):
        snapshot = lexicon.snapshot(self.conn)
        index = next(i for i,item in enumerate(snapshot['r1_terms']) if '의식 명료' in item['expr'])
        original = copy.deepcopy(snapshot['r1_terms'][index]['sets'])
        lexicon.add_alias(self.conn, lexicon.PROJECT_R1, 'r1:'+str(index), '의식 또렷하게 반응', 'fixture-reviewer')
        updated = lexicon.snapshot(self.conn)['r1_terms'][index]
        self.assertIn('의식 또렷하게 반응', updated['expr'])
        self.assertEqual(updated['sets'], original)

    def test_reference_import_is_searchable_but_does_not_change_runtime_dictionary(self):
        before = lexicon.snapshot(self.conn)
        result = self.load(self.csv())
        after = lexicon.snapshot(self.conn)
        self.assertEqual(result['imported'], 2)
        self.assertTrue(result['reference_only'])
        for key in ('catalog', 'r1_terms', 'commands'):
            self.assertEqual(before[key], after[key])
        found = lexicon.search(self.conn, 'chest pain', source_id='official.fixture')
        self.assertEqual(found['total'], 1)
        self.assertEqual(found['items'][0]['code'], 'R079')
        self.assertEqual(found['items'][0]['source_version'], '20250930')
        self.assertTrue(found['items'][0]['reference_only'])
        self.assertFalse(found['items'][0]['runtime_enabled'])
        self.assertEqual(found['items'][0]['license'], 'Fixture only')

    def test_one_official_code_can_have_several_names_with_distinct_term_ids(self):
        path = self.csv(rows=[['A000', '콜레라', 'Cholera'], ['A000', '다른 콜레라 명칭', 'Cholera synonym'], ['A000', '콜레라', 'Cholera']])
        result = self.load(path)
        found = lexicon.search(self.conn, 'A000', source_id='official.fixture')
        self.assertEqual(result['imported'], 2)
        self.assertEqual(found['total'], 2)
        self.assertEqual({item['code'] for item in found['items']}, {'A000'})
        self.assertEqual(len({item['term_id'] for item in found['items']}), 2)

    def test_identical_reference_reimport_does_not_duplicate_rows(self):
        path = self.csv()
        self.load(path)
        before = self.rows()
        self.load(path)
        self.assertEqual(before, self.rows())

    def test_csv_decode_and_late_row_errors_roll_back_new_source_activation(self):
        self.load(self.csv('initial.csv'))
        before = self.rows()
        broken = self.csv('broken.csv', rows=[['A001','유효한 앞 행','Valid row'],['A002','','Missing Korean label']])
        with self.assertRaises(ServiceError):
            self.load(broken, version='new-broken')
        self.assertEqual(before, self.rows())
        invalid_encoding = Path(self.tmp.name)/'bad-encoding.csv'
        invalid_encoding.write_bytes(b'\xff\xff\xff')
        with self.assertRaises(ServiceError):
            self.load(invalid_encoding, version='new-decode-error')
        self.assertEqual(before, self.rows())

    def test_new_reference_version_retains_history_and_selects_only_latest_active(self):
        self.load(self.csv('old.csv', rows=[['A000','이전 이름','Old name']]), version='v1')
        self.load(self.csv('new.csv', rows=[['A000','새 이름','New name']]), version='v2')
        self.assertEqual(lexicon.search(self.conn, '이전 이름', source_id='official.fixture')['total'], 0)
        self.assertEqual(lexicon.search(self.conn, '새 이름', source_id='official.fixture')['items'][0]['source_version'], 'v2')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM voice_terms WHERE source_id='official.fixture'").fetchone()[0], 2)

    def test_same_reference_version_cannot_be_overwritten_by_different_file(self):
        self.load(self.csv('one.csv'))
        before = self.rows()
        with self.assertRaises(ServiceError) as error:
            self.load(self.csv('different.csv', rows=[['A000','변경 이름','Changed']]))
        self.assertEqual(error.exception.code, 'VOICE_DICTIONARY_VERSION_CONFLICT')
        self.assertEqual(before, self.rows())

    def test_reference_terms_cannot_be_directly_activated_as_project_aliases(self):
        self.load(self.csv())
        with self.assertRaises(ServiceError):
            lexicon.add_alias(self.conn, 'official.fixture', 'A000', '흉통', 'fixture-reviewer')
        with self.assertRaises(ServiceError):
            self.load(self.csv(), source_id=lexicon.PROJECT_TERMS)

    def test_cp949_headers_and_korean_names_are_supported(self):
        result = self.load(self.csv(encoding='cp949'), encoding='cp949')
        self.assertEqual(result['imported'], 2)
        self.assertEqual(lexicon.search(self.conn, '콜레라', source_id='official.fixture')['total'], 1)

    def test_reference_metadata_preserves_distinct_concepts_and_stays_outside_parser(self):
        path = Path(self.tmp.name)/'concepts.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['상병기호', '한글명', '영문명', '개념코드'])
            writer.writerows([['K001','같은 용어','Same term','C001'],
                              ['K001','같은 용어','Same term','C002'],
                              ['K001','같은 용어','Same term','C001']])
        before = lexicon.snapshot(self.conn)
        result = self.load(path, extra_columns={'concept_code':'개념코드'})
        self.assertEqual(result['imported'], 2)
        found = lexicon.search(self.conn, 'K001', source_id='official.fixture')
        self.assertEqual(len({item['term_id'] for item in found['items']}), 2)
        self.assertEqual({item['reference_fields']['concept_code'] for item in found['items']}, {'C001','C002'})
        after = lexicon.snapshot(self.conn)
        for key in ('catalog', 'r1_terms', 'commands'):
            self.assertEqual(before[key], after[key])
        rows_before = self.rows()
        self.load(path, extra_columns={'concept_code':'개념코드'})
        self.assertEqual(rows_before, self.rows())
        with self.assertRaises(ServiceError) as error:
            self.load(path)
        self.assertEqual(error.exception.code, 'VOICE_DICTIONARY_VERSION_CONFLICT')

    def test_reference_metadata_requires_headers_and_bounded_values_atomically(self):
        initial = self.csv('initial-metadata.csv')
        self.load(initial)
        before = self.rows()
        with self.assertRaises(ServiceError):
            self.load(initial, version='missing-header', extra_columns={'concept_code':'개념코드'})
        self.assertEqual(before, self.rows())
        path = Path(self.tmp.name)/'long-metadata.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['상병기호', '한글명', '영문명', '개념코드'])
            writer.writerows([['K001','유효 용어','Valid','C001'],['K002','다음 용어','Next','C'*2001]])
        with self.assertRaises(ServiceError):
            self.load(path, version='bad-value', extra_columns={'concept_code':'개념코드'})
        self.assertEqual(before, self.rows())
        with self.assertRaises(ServiceError):
            self.load(path, version='bad-key', extra_columns={'concept code':'개념코드'})
        self.assertEqual(before, self.rows())

    def test_literal_search_escaping_and_bounded_pagination(self):
        self.load(self.csv(rows=[['X1','100% 자료','Under_score'],['X2','일반 자료','Normal']]))
        self.assertEqual(lexicon.search(self.conn, '%', source_id='official.fixture')['total'], 1)
        self.assertEqual(lexicon.search(self.conn, '_', source_id='official.fixture')['total'], 1)
        result = lexicon.search(self.conn, '자료', source_id='official.fixture', limit=1)
        self.assertTrue(result['has_more'])
        second = lexicon.search(self.conn, '자료', source_id='official.fixture', limit=1, offset=1)
        self.assertNotEqual(result['items'][0]['term_id'], second['items'][0]['term_id'])
        http_query = lexicon.search(self.conn, '자료', source_id='official.fixture', limit='1', offset='0')
        self.assertEqual(http_query['limit'], 1)
        self.assertEqual(http_query['offset'], 0)
        for args in ({'query':''}, {'query':'x','limit':True}, {'query':'x','limit':101}, {'query':'x','offset':-1}):
            with self.subTest(args=args), self.assertRaises(ServiceError):
                lexicon.search(self.conn, **args)
        for query in ('01', '-1', ' 1', '1.0', '١', '1000000000000000000000'):
            with self.subTest(limit=query), self.assertRaises(ServiceError):
                lexicon.search(self.conn, 'x', limit=query)

    def test_disabled_literal_alias_is_not_reenabled_by_seed(self):
        self.conn.execute("UPDATE voice_term_aliases SET enabled=0 WHERE alias='맥박'")
        lexicon.seed(self.conn)
        self.assertNotIn('맥박', lexicon.snapshot(self.conn)['catalog']['terms']['measurements']['pulse']['synonyms'])

    def test_alias_operation_failure_preserves_outer_transaction(self):
        self.conn.execute('CREATE TABLE fixture_outer(value TEXT)')
        self.conn.execute("INSERT INTO fixture_outer VALUES('pending')")
        with self.assertRaises(ServiceError):
            lexicon.add_alias(self.conn, lexicon.PROJECT_TERMS, 'measurement:pulse', '혈당', 'fixture-reviewer')
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(self.conn.execute('SELECT value FROM fixture_outer').fetchone()[0], 'pending')


if __name__ == '__main__':
    unittest.main()
