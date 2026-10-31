"""Deterministic extraction, transcription, and input-timing metrics.

These metrics compare supplied annotations; they do not establish clinical truth
or measure treatment outcomes. A wrong field contributes one false positive and
one false negative. Unmentioned symptoms are never inferred to be negative.
All ratios with a zero denominator are ``None``. Percentiles use nearest rank:
the sorted value at ``ceil(p * n) - 1`` (zero based), including for small samples.
"""

from __future__ import annotations

import math
import statistics
import unicodedata
from collections import defaultdict
from typing import Any


# Canonical production names are required. Legacy aliases remain independently
# scoreable but are never silently mapped to, or substituted for, these fields.
NUMERIC_FIELDS = frozenset({
    "age", "systolic_bp", "diastolic_bp", "pulse", "spo2", "temperature",
    "respiratory_rate", "glucose", "onset_at", "sys", "diast", "temp", "RR", "onset",
})
SYMPTOM_FIELDS = {
    "symptoms": "positive",
    "denied_symptoms": "negative",
    "uncertain_symptoms": "uncertain",
}
_TIMESTAMPS = (
    "input_start_ms", "speech_start_ms", "speech_end_ms", "stt_final_ms",
    "review_done_ms", "submit_done_ms",
)
_TIMING_METRICS = (
    "speech_duration_ms", "stt_after_speech_ms", "total_input_ms",
    "review_ms", "correction_count",
)


def _validate_json(value: Any, path: str) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path}: numbers must be finite")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_json(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path}: object keys must be strings")
            _validate_json(item, f"{path}.{key}")
        return
    raise ValueError(f"{path}: unsupported JSON type {type(value).__name__}")


def _validate_prediction(value: dict, label: str) -> None:
    _validate_json(value, label)
    if type(value) is not dict:
        raise ValueError(f"{label}: expected an object")
    if type(value.get("action")) is not str:
        raise ValueError(f"{label}.action: expected a string")
    if type(value.get("fields")) is not dict:
        raise ValueError(f"{label}.fields: expected an object")
    if value.get("review_field") is not None and type(value["review_field"]) is not str:
        raise ValueError(f"{label}.review_field: expected a string or null")
    for field in SYMPTOM_FIELDS:
        if field in value["fields"]:
            symptoms = value["fields"][field]
            if type(symptoms) is not list or any(type(item) is not str for item in symptoms):
                raise ValueError(f"{label}.fields.{field}: expected an array of strings")


def _number(value: Any) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _equal(left: Any, right: Any) -> bool:
    """JSON equality with equal int/float values, without bool/int coercion."""
    if _number(left) and _number(right):
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _symptoms(fields: dict) -> set[tuple[str, str]]:
    return {
        (symptom, polarity)
        for field, polarity in SYMPTOM_FIELDS.items()
        for symptom in fields.get(field, [])
    }


def _symptom_items(values: set[tuple[str, str]]) -> list[dict]:
    return [{"symptom": symptom, "polarity": polarity} for symptom, polarity in sorted(values)]


def _difference(left: Any, right: Any) -> Any:
    if not (_number(left) and _number(right)):
        return None
    try:
        result = left - right
    except OverflowError:
        return None
    return result if type(result) is int or math.isfinite(result) else None


def score_prediction(expected: dict, predicted: dict) -> dict:
    """Compare one structured prediction with its explicitly supplied annotation.

    Symptom arrays are sets, so their order and duplicate entries do not affect
    correctness. ``numeric_errors`` contains wrong *present* values; absent and
    extra numeric fields are reported separately. Absolute errors are available
    only when both values are actual JSON numbers (booleans are not numbers).
    """
    _validate_prediction(expected, "expected")
    _validate_prediction(predicted, "predicted")
    expected_fields, predicted_fields = expected["fields"], predicted["fields"]
    correct, wrong, missing, unexpected = [], [], [], []
    numeric_errors = []
    for field in sorted(expected_fields):
        if field not in predicted_fields:
            missing.append(field)
            continue
        reference, hypothesis = expected_fields[field], predicted_fields[field]
        equal = set(reference) == set(hypothesis) if field in SYMPTOM_FIELDS else _equal(reference, hypothesis)
        if equal:
            correct.append(field)
        else:
            wrong.append({"field": field, "expected": reference, "predicted": hypothesis})
            if field in NUMERIC_FIELDS:
                delta = _difference(hypothesis, reference)
                numeric_errors.append({
                    "field": field, "expected": reference, "predicted": hypothesis,
                    "signed_error": delta,
                    "absolute_error": abs(delta) if delta is not None else None,
                })
    for field in sorted(predicted_fields.keys() - expected_fields.keys()):
        unexpected.append({"field": field, "predicted": predicted_fields[field]})

    expected_symptoms, predicted_symptoms = _symptoms(expected_fields), _symptoms(predicted_fields)
    flips = [
        {"symptom": symptom, "expected": reference, "predicted": hypothesis}
        for symptom, reference in sorted(expected_symptoms)
        for predicted_symptom, hypothesis in sorted(predicted_symptoms)
        if symptom == predicted_symptom and reference != hypothesis
    ]
    action_correct = expected["action"] == predicted["action"]
    # edit/add command target metadata is not a readback target. Only review
    # commands compare review_field; other commands pass this applicability gate.
    review_correct = expected["action"] != "review" or expected.get("review_field") == predicted.get("review_field")
    return {
        "expected_action": expected["action"],
        "predicted_action": predicted["action"],
        "field_correct": correct, "field_wrong": wrong, "field_missing": missing,
        "field_unexpected": unexpected,
        "action_correct": action_correct, "review_field_correct": review_correct,
        "exact": action_correct and review_correct and not (wrong or missing or unexpected),
        "numeric_errors": numeric_errors,
        "numeric_missing": [field for field in missing if field in NUMERIC_FIELDS],
        "numeric_unexpected": [item for item in unexpected if item["field"] in NUMERIC_FIELDS],
        "symptom_correct": _symptom_items(expected_symptoms & predicted_symptoms),
        "symptom_missing": _symptom_items(expected_symptoms - predicted_symptoms),
        "symptom_unexpected": _symptom_items(predicted_symptoms - expected_symptoms),
        "symptom_polarity_flips": flips,
    }


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _rates(correct: int, wrong: int, missing: int, unexpected: int) -> dict:
    return {
        "precision": _ratio(correct, correct + wrong + unexpected),
        "recall": _ratio(correct, correct + wrong + missing),
        "f1": _ratio(2 * correct, 2 * correct + 2 * wrong + missing + unexpected),
    }


def _stats(values: list) -> dict:
    ordered = sorted(values)

    def average(items: list) -> Any:
        try:
            value = statistics.mean(items)
        except OverflowError:
            return None
        return value if type(value) is int or math.isfinite(value) else None

    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2 else average(ordered[middle - 1:middle + 1])) if ordered else None
    return {
        "n": len(ordered),
        "mean": average(ordered) if ordered else None,
        "median": median,
        "p95": ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else None,
    }


def _aggregate(rows: list[dict]) -> dict:
    counts = {name: sum(len(row[f"field_{name}"]) for row in rows)
              for name in ("correct", "wrong", "missing", "unexpected")}
    correct, wrong, missing, unexpected = (counts[key] for key in ("correct", "wrong", "missing", "unexpected"))
    counts.update({
        "false_positive": wrong + unexpected, "false_negative": wrong + missing,
        "expected": correct + wrong + missing, "predicted": correct + wrong + unexpected,
    })
    rates = _rates(correct, wrong, missing, unexpected)
    commands = [row for row in rows if row["expected_action"] != "dictation"]
    command_correct = sum(row["action_correct"] and row["review_field_correct"] for row in commands)
    action_correct = sum(row["action_correct"] for row in commands)
    reviews = [row for row in commands if row["expected_action"] == "review"]
    review_correct = sum(row["review_field_correct"] for row in reviews)
    symptom_counts = {name: sum(len(row.get(f"symptom_{name}", [])) for row in rows)
                      for name in ("correct", "missing", "unexpected")}
    symptom_counts.update(_rates(symptom_counts["correct"], 0, symptom_counts["missing"], symptom_counts["unexpected"]))
    symptom_counts["polarity_flips"] = sum(len(row.get("symptom_polarity_flips", [])) for row in rows)
    exact_count = sum(row["exact"] for row in rows)
    return {
        "row_count": len(rows), "exact_count": exact_count,
        "exact_accuracy": _ratio(exact_count, len(rows)), "field_counts": counts,
        **{f"field_{key}": value for key, value in rates.items()},
        "command": {
            "n": len(commands), "correct": command_correct,
            "accuracy": _ratio(command_correct, len(commands)),
            "action_correct": action_correct, "action_accuracy": _ratio(action_correct, len(commands)),
            "review_field_n": len(reviews), "review_field_correct": review_correct,
            "review_field_accuracy": _ratio(review_correct, len(reviews)),
        },
        "numeric": {
            "wrong": sum(len(row.get("numeric_errors", [])) for row in rows),
            "missing": sum(len(row.get("numeric_missing", [])) for row in rows),
            "unexpected": sum(len(row.get("numeric_unexpected", [])) for row in rows),
        },
        "symptoms": symptom_counts,
    }


def summarize(rows: list[dict]) -> dict:
    """Return micro totals, per-family/per-field scores, and family macro means.

    Each row must come from :func:`score_prediction`, optionally with ``id`` and
    ``family``. Unlabeled rows use the ``unclassified`` family. Macro means give
    each family with a defined metric equal weight; zero-denominator families
    are excluded for that metric, and their count is exposed alongside the mean.
    Numeric absolute errors are summarized per field only because units differ.
    """
    families = defaultdict(list)
    field_counts = defaultdict(lambda: {key: 0 for key in ("correct", "wrong", "missing", "unexpected")})
    numeric_errors = defaultdict(list)
    for row in rows:
        if type(row.get("expected_action")) is not str:
            raise ValueError("summary row must contain expected_action from score_prediction")
        family = row.get("family", "unclassified")
        if type(family) is not str:
            raise ValueError("summary row family must be a string")
        families[family].append(row)
        for category in ("correct", "missing"):
            for field in row[f"field_{category}"]:
                field_counts[field][category] += 1
        for category in ("wrong", "unexpected"):
            for item in row[f"field_{category}"]:
                field_counts[item["field"]][category] += 1
        for item in row.get("numeric_errors", []):
            if item.get("absolute_error") is not None:
                numeric_errors[item["field"]].append(item["absolute_error"])
    family_results = {family: _aggregate(group) for family, group in sorted(families.items())}
    fields = {}
    for field, counts in sorted(field_counts.items()):
        fields[field] = {**counts, **_rates(**counts),
                         "false_positive": counts["wrong"] + counts["unexpected"],
                         "false_negative": counts["wrong"] + counts["missing"]}
        if field in NUMERIC_FIELDS:
            fields[field]["numeric"] = {
                "wrong": counts["wrong"], "missing": counts["missing"], "unexpected": counts["unexpected"],
                "absolute_error": _stats(numeric_errors[field]),
            }
    macro = {"family_count": len(family_results)}
    for key in ("field_precision", "field_recall", "field_f1", "exact_accuracy", "command_accuracy"):
        values = [result["command"]["accuracy"] if key == "command_accuracy" else result[key]
                  for result in family_results.values()]
        defined = [value for value in values if value is not None]
        macro[key] = statistics.mean(defined) if defined else None
        macro[f"{key}_families"] = len(defined)
    return {"overall": _aggregate(rows), "families": family_results, "fields": fields, "macro_family": macro}


def _edit_distance(reference: list | str, hypothesis: list | str) -> int:
    # Keep the dynamic-programming row on the shorter axis (O(min(n, m)) space).
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, reference_item in enumerate(reference, 1):
        current = [row]
        for column, hypothesis_item in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (reference_item != hypothesis_item)))
        previous = current
    return previous[-1]


def text_error(reference: str, hypothesis: str) -> dict:
    """Levenshtein CER/WER after Unicode NFKC and case folding.

    CER removes all Unicode whitespace. WER splits on Unicode whitespace after
    the same Unicode/case normalization. Punctuation, decimal points, minus
    signs, and slashes are retained. Empty references have undefined rates,
    even when the hypothesis is also empty. This is a textual comparison;
    spoken Korean numerals and digit notation are not semantically equated.
    """
    if type(reference) is not str or type(hypothesis) is not str:
        raise ValueError("reference and hypothesis must be strings")
    normalized_reference = unicodedata.normalize("NFKC", reference).casefold()
    normalized_hypothesis = unicodedata.normalize("NFKC", hypothesis).casefold()
    reference_words, hypothesis_words = normalized_reference.split(), normalized_hypothesis.split()
    reference_chars = "".join(reference_words)
    hypothesis_chars = "".join(hypothesis_words)
    edits = _edit_distance(reference_chars, hypothesis_chars)
    word_edits = _edit_distance(reference_words, hypothesis_words)
    return {
        "edits": edits, "ref_chars": len(reference_chars), "hyp_chars": len(hypothesis_chars),
        "cer": _ratio(edits, len(reference_chars)),
        "word_edits": word_edits, "ref_words": len(reference_words), "hyp_words": len(hypothesis_words),
        "wer": _ratio(word_edits, len(reference_words)),
        "normalized_reference": reference_chars, "normalized_hypothesis": hypothesis_chars,
        "normalized_reference_words": reference_words, "normalized_hypothesis_words": hypothesis_words,
    }


def _timing_number(value: Any) -> bool:
    if not _number(value):
        return False
    try:
        return math.isfinite(float(value)) and value >= 0
    except (OverflowError, ValueError):
        return False


def timing_summary(records: list[dict]) -> dict:
    """Summarize measured intervals from a common monotonic clock, in ms.

    Inputs may be flat timing objects or records containing a ``timing`` object.
    Missing timestamps yield ``None``, never zero. A negative/nonfinite timestamp
    or inconsistent chronological order invalidates all durations in that row
    and emits a warning. Correction counts are independently validated. Manual
    input requires no speech or STT timestamps. Means never impute missing data.
    """
    derived, warnings = [], []
    for index, source in enumerate(records):
        if type(source) is not dict:
            raise ValueError(f"timing record {index}: expected an object")
        record = dict(source)
        if "timing" in source:
            nested = source["timing"]
            if nested is not None and type(nested) is not dict:
                raise ValueError(f"timing record {index}.timing: expected an object or null")
            record.update(nested or {})
        method = record.get("method", "unknown")
        metadata = {key: record.get(key) for key in ("participant_id", "case_id", "session_id")}

        def warn(code: str, message: str, fields: list[str]) -> None:
            warnings.append({"record_index": index, **metadata, "code": code, "message": message, "fields": fields})

        if method not in ("voice", "manual"):
            warn("unknown_method", "method must be voice or manual; grouped as unknown", ["method"])
            method = "unknown"
        invalid = False
        present = []
        for key in _TIMESTAMPS:
            value = record.get(key)
            if value is None:
                continue
            if not _timing_number(value):
                warn("invalid_timestamp", "timestamp must be a finite nonnegative number", [key])
                invalid = True
                continue
            present.append((key, value))
        for (left_key, left), (right_key, right) in zip(present, present[1:]):
            if left > right:
                warn("timestamp_order", "timestamps must follow input, speech, STT, review, submit order", [left_key, right_key])
                invalid = True

        def duration(start: str, end: str) -> Any:
            if invalid or record.get(start) is None or record.get(end) is None:
                return None
            value = _difference(record[end], record[start])
            if value is None:
                warn("invalid_duration", "timestamp difference is not finite", [start, end])
            return value

        correction = record.get("correction_count")
        if correction is not None and (type(correction) is not int or correction < 0):
            warn("invalid_correction_count", "correction_count must be a nonnegative integer", ["correction_count"])
            correction = None
        derived.append({
            "record_index": index, "method": method, **metadata,
            "valid_timing": not invalid,
            "speech_duration_ms": duration("speech_start_ms", "speech_end_ms"),
            "stt_after_speech_ms": duration("speech_end_ms", "stt_final_ms"),
            "total_input_ms": duration("input_start_ms", "submit_done_ms"),
            "review_ms": duration("stt_final_ms", "review_done_ms"),
            "correction_count": correction,
        })

    def aggregate(group: list[dict]) -> dict:
        return {"record_count": len(group), **{
            key: _stats([row[key] for row in group if row[key] is not None])
            for key in _TIMING_METRICS
        }}

    methods = sorted({row["method"] for row in derived})
    return {
        "overall": aggregate(derived),
        "methods": {method: aggregate([row for row in derived if row["method"] == method]) for method in methods},
        "records": derived, "warnings": warnings,
        "percentile_method": "nearest_rank: sorted[ceil(0.95*n)-1]",
    }
