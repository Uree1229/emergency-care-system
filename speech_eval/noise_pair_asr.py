"""Offline, same-engine ASR of frozen synthetic raw/denoised WAV pairs.

This runner downloads nothing, uploads nothing, and never reads a microphone or
patient database. A local faster-whisper model directory is mandatory. Text
references score the untouched hypotheses; they are never decoder hints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import wave

from .metrics import text_error

DECODE = {"language": "ko", "beam_size": 5, "temperature": 0.0,
          "condition_on_previous_text": False, "vad_filter": False,
          "initial_prompt": None, "hotwords": None, "word_timestamps": False}
ACTIONS = {None, "stop", "recommend", "handoff_summary", "new_patient", "previous_patient"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)


def _asset(base, value):
    if type(value) is not dict or set(value) != {"path", "sha256"}:
        raise ValueError("Every audio asset requires only path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("WAV paths must stay below the frozen manifest directory")
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file() or digest(path) != value["sha256"]:
        raise ValueError("Audio file missing, outside manifest, or hash mismatch")
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
            raise ValueError("ASR pairs must be 16kHz mono PCM16 WAV")
        frames = wav.getnframes()
    if not 0 < frames <= 180 * 16000:
        raise ValueError("Each WAV must be nonempty and at most 180 seconds")
    return path, frames


def load_manifest(path):
    path = Path(path).resolve()
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if (value.get("schema") != 1 or value.get("purpose") != "synthetic_paired_denoise_asr"
            or value.get("synthetic_voice") is not True or value.get("human_voice") is not False):
        raise ValueError("Explicitly synthetic paired manifest required")
    pairs = value.get("pairs")
    if type(pairs) is not list or not 1 <= len(pairs) <= 100:
        raise ValueError("Frozen experiment requires 1..100 pairs")
    seen = set()
    for row in pairs:
        if (type(row) is not dict or type(row.get("id")) is not str or not row["id"]
                or row["id"] in seen or type(row.get("reference_text")) is not str
                or "expected_action" not in row or row.get("expected_action") not in ACTIONS
                or type(row.get("condition")) is not str or not row["condition"].strip()):
            raise ValueError("Malformed or duplicate pair")
        if not row["reference_text"].strip() and not row["condition"].startswith("noise_only_"):
            raise ValueError("A nonempty independent reference is required except for explicitly noise-only pairs")
        seen.add(row["id"])
        original, frames = _asset(path.parent, row["original"])
        processed, processed_frames = _asset(path.parent, row["processed"])
        if frames != processed_frames:
            raise ValueError("Paired WAV lengths must match")
    return value


def classify(texts, node, module):
    """Read-only classification using the application's actual command detector."""
    module = Path(module).resolve()
    source = """import {readFileSync} from 'node:fs';
const {findVoiceTrigger}=await import(process.argv[1]);
const texts=JSON.parse(readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(texts.map(text=>findVoiceTrigger(text))));"""
    result = subprocess.run([str(node), "--input-type=module", "-e", source, module.as_uri()],
                            input=json.dumps(texts, ensure_ascii=False), text=True,
                            encoding="utf-8", capture_output=True, check=True, timeout=60)
    values = json.loads(result.stdout)
    if type(values) is not list or len(values) != len(texts):
        raise ValueError("Command detector returned the wrong number of results")
    return values


def source_fingerprints(manifest_path, manifest, model_dir, command_module):
    """Hash every input actually used, including audio, local model and runner."""
    manifest_path, model_dir = Path(manifest_path), Path(model_dir)
    result = {"manifest": digest(manifest_path), "runner": digest(__file__),
              "command_module": digest(command_module)}
    result.update({"model:" + p.name: digest(p) for p in sorted(model_dir.iterdir()) if p.is_file()})
    for pair in manifest["pairs"]:
        for version in ("original", "processed"):
            result["audio:" + pair["id"] + ":" + version] = digest(manifest_path.parent / pair[version]["path"])
    return result


def run(manifest_path, model_dir, output, *, node, command_module, cpu_threads=4):
    manifest_path, model_dir, output = map(lambda p: Path(p).resolve(), (manifest_path, model_dir, output))
    manifest = load_manifest(manifest_path)
    if output.exists():
        raise FileExistsError("Refusing to overwrite an experiment")
    if not model_dir.is_dir() or not (model_dir / "model.bin").is_file():
        raise ValueError("An already downloaded local CTranslate2 model is required")
    if not 1 <= cpu_threads <= 32:
        raise ValueError("cpu_threads must be 1..32")
    # Probe the actual parser before expensive inference. No decoder hints here.
    classify([""], node, command_module)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    from faster_whisper import WhisperModel
    import importlib.metadata
    output.mkdir(parents=True)
    fingerprints_before = source_fingerprints(manifest_path, manifest, model_dir, command_module)
    frozen = {"manifest": manifest, "manifest_sha256": digest(manifest_path),
              "runner_sha256": digest(__file__), "command_module_sha256": digest(command_module),
              "model_files": {p.name: digest(p) for p in sorted(model_dir.iterdir()) if p.is_file()},
              "engine": "faster-whisper", "engine_version": importlib.metadata.version("faster-whisper"),
              "ctranslate2_version": importlib.metadata.version("ctranslate2"),
              "device": "cpu", "compute_type": "int8", "cpu_threads": cpu_threads,
              "decode": DECODE, "synthetic_voice": True, "human_voice": False,
              "source_hashes_before": fingerprints_before,
              "audio_uploads": 0, "paid_api_calls": 0,
              "warning": "Synthetic Korean TTS and generated noise; not human, field or continuous-stream command accuracy."}
    write_json(output / "frozen-plan.json", frozen)
    model = WhisperModel(str(model_dir), device="cpu", compute_type="int8",
                         cpu_threads=cpu_threads, num_workers=1, local_files_only=True)
    records = []
    with (output / "transcripts.jsonl").open("x", encoding="utf-8") as log:
        for pair in manifest["pairs"]:
            for version in ("original", "processed"):
                audio = manifest_path.parent / pair[version]["path"]
                started = time.perf_counter()
                segments, info = model.transcribe(str(audio), **DECODE)
                segments = [{"start": s.start, "end": s.end, "text": s.text,
                             "avg_logprob": s.avg_logprob, "no_speech_prob": s.no_speech_prob}
                            for s in segments]
                hypothesis = "".join(s["text"] for s in segments)
                record = {"pair_id": pair["id"], "condition": pair["condition"],
                          "version": version, "audio_sha256": pair[version]["sha256"],
                          "reference_text": pair["reference_text"], "expected_action": pair["expected_action"],
                          "hypothesis": hypothesis, "segments": segments, "language": info.language,
                          "language_probability": info.language_probability,
                          "decode_seconds": time.perf_counter() - started,
                          "text_error": text_error(pair["reference_text"], hypothesis)}
                records.append(record)
                log.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                log.flush()
                print(json.dumps({"finished": len(records), "total": len(manifest["pairs"]) * 2,
                                  "pair": pair["id"], "version": version}, ensure_ascii=False), flush=True)
    triggers = classify([r["hypothesis"] for r in records], node, command_module)
    for record, trigger in zip(records, triggers):
        record["command_result"] = trigger
        record["observed_action"] = trigger["action"] if trigger else None
        record["command_correct"] = record["observed_action"] == record["expected_action"]
    write_json(output / "scored-transcripts.json", records)
    groups = {}
    for condition in sorted({r["condition"] for r in records}):
        groups[condition] = {}
        for version in ("original", "processed"):
            selected = [r for r in records if r["condition"] == condition and r["version"] == version]
            ref_chars = sum(r["text_error"]["ref_chars"] for r in selected)
            groups[condition][version] = {"clips": len(selected),
                "char_edits": sum(r["text_error"]["edits"] for r in selected), "reference_chars": ref_chars,
                "micro_cer": sum(r["text_error"]["edits"] for r in selected) / ref_chars if ref_chars else None,
                "command_correct": sum(r["command_correct"] for r in selected),
                "positive_correct": sum(r["command_correct"] for r in selected if r["expected_action"] is not None),
                "positive_count": sum(r["expected_action"] is not None for r in selected),
                "negative_false_commands": sum(r["observed_action"] is not None for r in selected if r["expected_action"] is None),
                "negative_count": sum(r["expected_action"] is None for r in selected),
                "nonempty_hypotheses": sum(bool(r["hypothesis"].strip()) for r in selected)}
    paired = [{"id": records[i]["pair_id"], "condition": records[i]["condition"],
               "cer_delta_processed_minus_original": (records[i+1]["text_error"]["cer"] - records[i]["text_error"]["cer"])
                    if records[i]["text_error"]["cer"] is not None else None,
               "original_action": records[i]["observed_action"], "processed_action": records[i+1]["observed_action"],
               "original_correct": records[i]["command_correct"], "processed_correct": records[i+1]["command_correct"]}
              for i in range(0, len(records), 2)]
    fingerprints_after = source_fingerprints(manifest_path, manifest, model_dir, command_module)
    unchanged = fingerprints_before == fingerprints_after
    summary = {"completed": unchanged, "pairs": len(paired), "actual_local_asr_calls": len(records),
               "audio_uploads": 0, "paid_api_calls": 0, "groups": groups, "paired": paired,
               "frozen_plan_sha256": digest(output / "frozen-plan.json"),
               "transcripts_sha256": digest(output / "transcripts.jsonl"),
               "source_hashes_after": fingerprints_after, "source_hashes_unchanged": unchanged,
               "limits": ["Finite synthetic clips, not continuous-stream or false-accepts/hour evaluation.",
                          "Korean numeral words and digit spellings are not semantically equated in CER.",
                          "No truth text, hotwords, initial prompt, VAD or previous-clip conditioning supplied to ASR.",
                          "One small multilingual base engine and one synthetic voice do not establish field generalization."]}
    write_json(output / "summary.json", summary)
    if not unchanged:
        raise ValueError("An audio asset, local model, manifest, parser or runner changed during inference")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--local-model-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--command-module", default=str(Path(__file__).resolve().parents[1] / "paramedic-ui/recording-control.mjs"))
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()
    result = run(args.manifest, args.local_model_dir, args.output, node=args.node,
                 command_module=args.command_module, cpu_threads=args.cpu_threads)
    print(json.dumps({"completed": result["completed"], "pairs": result["pairs"],
                      "actual_local_asr_calls": result["actual_local_asr_calls"]}))


if __name__ == "__main__":
    main()
