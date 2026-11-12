"""Checks for the independent replay oracle, without executing the corpus."""
import unittest
from voice_lab.evaluate_stress_corpus_20261030 import equal, validate


class OracleTests(unittest.TestCase):
    def test_exact_values_do_not_coerce_or_drop_extra_array_members(self):
        self.assertTrue(equal(0, 0))
        self.assertFalse(equal(None, 0))
        self.assertFalse(equal(False, 0))
        self.assertFalse(equal('97', 97))
        self.assertTrue(equal(['흉통', '구토'], ['구토', '흉통']))
        self.assertFalse(equal(['흉통', '구토'], ['흉통']))
        self.assertFalse(equal(['흉통', '흉통'], ['흉통']))

    def corpus(self):
        return {'synthetic': True, 'now': 2_000_000_000,
                'provenance': {'extractor_results_used': False, 'server_or_stt_called': False},
                'long_cases': [{'id': 'L', 'text': '합성 문장', 'parts': ['합성 ', '문장'],
                                'expected_fields': {'age': 0}, 'forbidden_fields': []}],
                'boundary_cases': [], 'flows': []}

    def test_validation_rejects_colliding_expectations_and_noncontract_keys(self):
        corpus = self.corpus()
        self.assertEqual(validate(corpus)['utterance_count'], 1)
        corpus['long_cases'][0]['forbidden_fields'] = ['age']
        with self.assertRaises(AssertionError):
            validate(corpus)
        corpus['long_cases'][0]['forbidden_fields'] = []
        corpus['long_cases'][0]['expected_fields'] = {'vitals.pulse': 92}
        with self.assertRaises(AssertionError):
            validate(corpus)

    def test_long_parts_must_be_lossless_and_each_text_within_api_limit(self):
        corpus = self.corpus()
        corpus['long_cases'][0]['parts'] = ['다른 본문']
        with self.assertRaises(AssertionError):
            validate(corpus)
        corpus['long_cases'][0]['text'] = '가'*4001
        corpus['long_cases'][0]['parts'] = ['가'*4001]
        with self.assertRaises(AssertionError):
            validate(corpus)


if __name__ == '__main__':
    unittest.main()
