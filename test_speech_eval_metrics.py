"""Unit checks for annotation metrics; no recognizer or external dependency."""

import json
import math
import unittest

from speech_eval.metrics import score_prediction, summarize, text_error, timing_summary


def prediction(fields=None, action="dictation", review_field=None):
    return {"action": action, "fields": fields or {}, "review_field": review_field}


class StructuredMetricsTests(unittest.TestCase):
    def test_wrong_value_is_false_positive_and_false_negative(self):
        row = score_prediction(prediction({"age": 50, "sys": 120, "pulse": 80}),
                               prediction({"age": 50, "sys": 130, "temp": 37}))
        summary = summarize([row])["overall"]
        self.assertEqual(summary["field_counts"], {
            "correct": 1, "wrong": 1, "missing": 1, "unexpected": 1,
            "false_positive": 2, "false_negative": 2, "expected": 3, "predicted": 3,
        })
        self.assertAlmostEqual(summary["field_precision"], 1 / 3)
        self.assertAlmostEqual(summary["field_recall"], 1 / 3)
        self.assertEqual(row["numeric_errors"][0]["absolute_error"], 10)
        self.assertEqual(row["numeric_missing"], ["pulse"])
        self.assertEqual(row["numeric_unexpected"], [{"field": "temp", "predicted": 37}])
        self.assertEqual(summary["numeric"], {"wrong": 1, "missing": 1, "unexpected": 1})

    def test_boolean_is_not_a_number_and_recursive_json_equality(self):
        row = score_prediction(prediction({"age": 1, "meta": {"x": [1, False]}}),
                               prediction({"age": True, "meta": {"x": [1.0, 0]}}))
        self.assertEqual([item["field"] for item in row["field_wrong"]], ["age", "meta"])
        self.assertIsNone(row["numeric_errors"][0]["absolute_error"])
        self.assertTrue(score_prediction(prediction({"age": 1}), prediction({"age": 1.0}))["exact"])

    def test_strict_finite_json(self):
        bad_values = [float("nan"), float("inf"), {1: "x"}, (1, 2), {1, 2}, b"x"]
        for bad in bad_values:
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                score_prediction(prediction({"x": bad}), prediction())
        with self.assertRaises(ValueError):
            score_prediction(prediction({"symptoms": "기침"}), prediction())

    def test_symptom_order_duplicate_and_negative_are_distinct(self):
        expected = prediction({"symptoms": ["기침", "발열"], "denied_symptoms": ["호흡곤란"]})
        reordered = prediction({"symptoms": ["발열", "기침", "기침"], "denied_symptoms": ["호흡곤란"]})
        self.assertTrue(score_prediction(expected, reordered)["exact"])
        result = score_prediction(expected, prediction({"symptoms": ["기침", "호흡곤란", "흉통"]}))
        self.assertEqual(result["symptom_correct"], [{"symptom": "기침", "polarity": "positive"}])
        self.assertIn({"symptom": "발열", "polarity": "positive"}, result["symptom_missing"])
        self.assertIn({"symptom": "호흡곤란", "polarity": "negative"}, result["symptom_missing"])
        self.assertEqual(result["symptom_polarity_flips"], [
            {"symptom": "호흡곤란", "expected": "negative", "predicted": "positive"}])
        self.assertIn({"symptom": "흉통", "polarity": "positive"}, result["symptom_unexpected"])
        self.assertFalse(any(item["symptom"] == "흉통" for item in result["symptom_polarity_flips"]))

    def test_uncertainty_is_an_explicit_annotation(self):
        result = score_prediction(prediction({"uncertain_symptoms": ["발열"]}),
                                  prediction({"denied_symptoms": ["발열"]}))
        self.assertEqual(result["symptom_polarity_flips"], [
            {"symptom": "발열", "expected": "uncertain", "predicted": "negative"}])
        self.assertEqual(result["symptom_correct"], [])

    def test_command_includes_review_field_and_excludes_dictation(self):
        command = score_prediction(prediction(action="review", review_field="age"),
                                   prediction(action="review", review_field="sys"))
        self.assertTrue(command["action_correct"])
        self.assertFalse(command["review_field_correct"])
        self.assertFalse(command["exact"])
        summary = summarize([command, score_prediction(prediction(), prediction())])["overall"]
        self.assertEqual(summary["command"]["n"], 1)
        self.assertEqual(summary["command"]["accuracy"], 0)
        self.assertEqual(summary["command"]["action_accuracy"], 1)
        self.assertIsNone(summary["field_precision"])
        self.assertIsNone(summarize([score_prediction(prediction(), prediction())])["overall"]["command"]["accuracy"])

    def test_edit_metadata_target_does_not_become_review_target(self):
        edit = score_prediction(prediction({"pulse": 92}, action="edit"),
                                prediction({"pulse": 92}, action="edit", review_field="pulse"))
        self.assertTrue(edit["review_field_correct"])
        self.assertTrue(edit["exact"])
        review = score_prediction(prediction(action="review", review_field="blood_pressure"),
                                  prediction(action="review", review_field="pulse"))
        self.assertFalse(review["review_field_correct"])
        self.assertFalse(review["exact"])
        summary = summarize([edit, review])["overall"]["command"]
        self.assertEqual(summary["accuracy"], 0.5)
        self.assertEqual(summary["review_field_n"], 1)
        self.assertEqual(summary["review_field_accuracy"], 0)
        self.assertIsNone(summarize([edit])["overall"]["command"]["review_field_accuracy"])

    def test_every_production_numeric_field_is_counted(self):
        expected_values = {
            "age": 40, "systolic_bp": 120, "diastolic_bp": 80, "pulse": 92,
            "spo2": 98, "temperature": 36.5, "respiratory_rate": 20,
            "glucose": 100, "onset_at": 1793332700,
        }
        rows = []
        for field, value in expected_values.items():
            with self.subTest(field=field):
                row = score_prediction(prediction({field: value}), prediction({field: value + 1}))
                self.assertEqual(row["numeric_errors"][0]["field"], field)
                self.assertEqual(row["numeric_errors"][0]["absolute_error"], 1)
                missing = score_prediction(prediction({field: value}), prediction())
                self.assertEqual(missing["numeric_missing"], [field])
                unexpected = score_prediction(prediction(), prediction({field: value}))
                self.assertEqual(unexpected["numeric_unexpected"], [{"field": field, "predicted": value}])
                rows.append(row)
        result = summarize(rows)
        self.assertEqual(result["overall"]["numeric"]["wrong"], len(expected_values))
        for field in expected_values:
            self.assertEqual(result["fields"][field]["numeric"]["absolute_error"]["mean"], 1)

    def test_micro_and_family_macro_have_different_weights(self):
        common = [{**score_prediction(prediction({"age": 40}), prediction({"age": 40})), "family": "common"}
                  for _ in range(9)]
        rare = {**score_prediction(prediction({"age": 40}), prediction({"age": 41})), "family": "rare"}
        result = summarize([*common, rare])
        self.assertEqual(result["overall"]["field_precision"], 0.9)
        self.assertEqual(result["macro_family"]["field_precision"], 0.5)
        self.assertEqual(result["macro_family"]["field_precision_families"], 2)
        self.assertEqual(result["fields"]["age"]["numeric"]["absolute_error"]["mean"], 1)

    def test_empty_and_absent_denominators_are_undefined(self):
        result = summarize([])
        self.assertIsNone(result["overall"]["exact_accuracy"])
        self.assertIsNone(result["overall"]["field_precision"])
        self.assertIsNone(result["macro_family"]["field_recall"])
        self.assertEqual(result["families"], {})
        extra = summarize([score_prediction(prediction(), prediction({"age": 30}))])["overall"]
        self.assertEqual(extra["field_precision"], 0)
        self.assertIsNone(extra["field_recall"])
        json.dumps(result, allow_nan=False)

    def test_large_finite_values_do_not_create_nonfinite_errors(self):
        result = score_prediction(prediction({"age": 10 ** 400}), prediction({"age": 1.0}))
        self.assertIsNone(result["numeric_errors"][0]["absolute_error"])
        json.dumps(summarize([result]), allow_nan=False)


class TextMetricsTests(unittest.TestCase):
    def test_korean_nfkc_whitespace_and_case(self):
        result = text_error("체온 ３７.５ ABC", "체 온 37.5 abc")
        self.assertEqual(result["edits"], 0)
        self.assertEqual(result["cer"], 0)
        self.assertGreater(result["wer"], 0)
        self.assertEqual(result["normalized_reference"], "체온37.5abc")
        composed = text_error("가", "가")
        self.assertEqual(composed["cer"], 0)

    def test_medical_punctuation_is_preserved(self):
        result = text_error("-1.5/120", "15 120")
        self.assertEqual(result["normalized_reference"], "-1.5/120")
        self.assertEqual(result["edits"], 3)
        self.assertEqual(result["ref_chars"], 8)
        self.assertEqual(result["cer"], 3 / 8)

    def test_known_edit_distance_and_empty_references(self):
        self.assertEqual(text_error("kitten", "sitting")["edits"], 3)
        self.assertEqual(text_error("가 나", "가 다 나")["word_edits"], 1)
        for hypothesis in ("", "입력"):
            result = text_error(" \t\n", hypothesis)
            self.assertIsNone(result["cer"])
            self.assertIsNone(result["wer"])
        self.assertEqual(text_error("입력", "")["cer"], 1)


class TimingMetricsTests(unittest.TestCase):
    def test_voice_manual_and_missing_are_separate(self):
        result = timing_summary([
            {"method": "voice", "input_start_ms": 100, "speech_start_ms": 200,
             "speech_end_ms": 1200, "stt_final_ms": 1500, "review_done_ms": 1800,
             "submit_done_ms": 2000, "correction_count": 2},
            {"method": "manual", "input_start_ms": 10, "submit_done_ms": 2010, "correction_count": 0},
            {"method": "voice", "speech_start_ms": 0, "speech_end_ms": 200},
        ])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["methods"]["voice"]["speech_duration_ms"]["mean"], 600)
        self.assertEqual(result["methods"]["manual"]["total_input_ms"]["mean"], 2000)
        self.assertEqual(result["methods"]["manual"]["stt_after_speech_ms"]["n"], 0)
        self.assertIsNone(result["methods"]["manual"]["stt_after_speech_ms"]["mean"])
        self.assertIsNone(result["records"][2]["total_input_ms"])
        self.assertEqual(result["overall"]["total_input_ms"]["n"], 2)
        self.assertEqual(result["overall"]["review_ms"]["mean"], 300)

    def test_invalid_order_and_negative_are_not_zero(self):
        result = timing_summary([
            {"method": "voice", "input_start_ms": 0, "speech_start_ms": 100,
             "speech_end_ms": 200, "stt_final_ms": 150, "submit_done_ms": 500},
            {"method": "manual", "input_start_ms": -10, "submit_done_ms": 10},
            {"method": "manual", "input_start_ms": True, "submit_done_ms": 10},
            {"method": "voice", "speech_start_ms": 0, "speech_end_ms": math.inf},
        ])
        self.assertEqual(len(result["warnings"]), 4)
        self.assertEqual(result["overall"]["total_input_ms"]["n"], 0)
        self.assertIsNone(result["overall"]["total_input_ms"]["mean"])
        self.assertTrue(all(row["total_input_ms"] is None for row in result["records"]))
        self.assertTrue(all(row["valid_timing"] is False for row in result["records"]))
        json.dumps(result, allow_nan=False)

    def test_nested_timing_nearest_rank_and_real_zero(self):
        result = timing_summary([
            {"method": "manual", "case_id": str(index),
             "timing": {"input_start_ms": 0, "submit_done_ms": index}}
            for index in range(1, 21)
        ])
        self.assertEqual(result["overall"]["total_input_ms"]["median"], 10.5)
        self.assertEqual(result["overall"]["total_input_ms"]["p95"], 19)
        zero = timing_summary([{"method": "manual", "input_start_ms": 0, "submit_done_ms": 0}])
        self.assertEqual(zero["overall"]["total_input_ms"]["mean"], 0)
        self.assertEqual(zero["overall"]["total_input_ms"]["n"], 1)

    def test_empty_or_invalid_corrections(self):
        result = timing_summary([{"method": "manual"}, {"method": "manual", "correction_count": -1}])
        self.assertEqual(result["overall"]["correction_count"]["n"], 0)
        self.assertIsNone(result["overall"]["correction_count"]["mean"])
        self.assertEqual(result["warnings"][0]["code"], "invalid_correction_count")
        self.assertIsNone(timing_summary([])["overall"]["total_input_ms"]["p95"])

    def test_large_finite_timestamps_keep_summary_finite(self):
        result = timing_summary([{"method": "manual", "input_start_ms": 0, "submit_done_ms": 1e308}] * 2)
        self.assertEqual(result["overall"]["total_input_ms"]["median"], 1e308)
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
