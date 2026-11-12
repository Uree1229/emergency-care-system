"""Guardrails for the optional offline paired-ASR runner; no model or network."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import wave

from speech_eval.noise_pair_asr import DECODE, load_manifest, source_fingerprints
from speech_eval.metrics import text_error


class NoisePairManifestTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[2] / '.codex-build' / 'noise-pair-asr-unit'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("a.wav", "b.wav"):
            with wave.open(str(self.root/name), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\x01\x00"*160)
        self.value = {"schema":1,"purpose":"synthetic_paired_denoise_asr",
                      "synthetic_voice":True,"human_voice":False,"pairs":[{
                          "id":"p1","reference_text":"종료하지 마세요.","expected_action":None,"condition":"quiet",
                          **{key:{"path":name,"sha256":hashlib.sha256((self.root/name).read_bytes()).hexdigest()}
                             for key,name in [("original","a.wav"),("processed","b.wav")]}}]}
    def load(self):
        path=self.root/'manifest.json';path.write_text(json.dumps(self.value),encoding='utf-8')
        return load_manifest(path)
    def test_matching_pair_and_independent_reference_are_preserved(self):
        self.assertEqual(self.load(),self.value)
        self.assertEqual(text_error("종료하지 마세요.","종료하세요.")["edits"],2)
    def test_modified_audio_is_rejected_before_any_engine_is_loaded(self):
        (self.root/'b.wav').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'hash mismatch'): self.load()
    def test_duplicate_pair_cannot_silently_double_weight_results(self):
        self.value['pairs'].append(dict(self.value['pairs'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate'): self.load()
    def test_audio_path_cannot_escape_manifest_directory(self):
        self.value['pairs'][0]['processed']['path']='../outside.wav'
        with self.assertRaisesRegex(ValueError,'below'): self.load()
    def test_different_pair_length_is_rejected(self):
        path=self.root/'b.wav'
        with wave.open(str(path),'wb') as h:
            h.setparams((1,2,16000,0,'NONE','not compressed'));h.writeframes(b'\0\0'*80)
        self.value['pairs'][0]['processed']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'lengths'): self.load()
    def test_live_or_unlabelled_human_audio_is_out_of_scope(self):
        self.value['human_voice']=True
        with self.assertRaisesRegex(ValueError,'synthetic'): self.load()
    def test_missing_command_label_is_not_silently_treated_as_negative(self):
        del self.value['pairs'][0]['expected_action']
        with self.assertRaisesRegex(ValueError,'Malformed'): self.load()
    def test_only_explicit_noise_only_pairs_may_have_empty_references(self):
        self.value['pairs'][0]['reference_text']=''
        with self.assertRaisesRegex(ValueError,'reference'): self.load()
        self.value['pairs'][0]['condition']='noise_only_engine'
        self.assertEqual(self.load()['pairs'][0]['expected_action'],None)
    def test_integrity_includes_changed_model_and_audio_not_only_manifest(self):
        self.load();model=self.root/'model';model.mkdir();(model/'model.bin').write_bytes(b'model')
        parser=self.root/'parser.mjs';parser.write_text('export {};',encoding='utf-8')
        before=source_fingerprints(self.root/'manifest.json',self.value,model,parser)
        (model/'model.bin').write_bytes(b'changed');(self.root/'b.wav').write_bytes(b'changed')
        after=source_fingerprints(self.root/'manifest.json',self.value,model,parser)
        self.assertNotEqual(before['model:model.bin'],after['model:model.bin'])
        self.assertNotEqual(before['audio:p1:processed'],after['audio:p1:processed'])
        self.assertEqual(before['manifest'],after['manifest'])
    def test_decoder_has_no_reference_text_hint_or_vad_selection(self):
        self.assertEqual(DECODE['language'],'ko')
        self.assertIsNone(DECODE['initial_prompt']);self.assertIsNone(DECODE['hotwords'])
        self.assertFalse(DECODE['condition_on_previous_text']);self.assertFalse(DECODE['vad_filter'])


if __name__=='__main__': unittest.main()
