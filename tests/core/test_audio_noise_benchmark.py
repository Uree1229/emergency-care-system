"""Signal-oracle tests for synthetic noise screening, not STT accuracy tests."""
import tempfile
from pathlib import Path
import unittest

import numpy as np

from speech_eval import audio_noise_benchmark as noise


class SignalTests(unittest.TestCase):
    def source(self,n=16000):
        t=np.arange(n)/16000
        return .2*np.sin(2*np.pi*180*t)+.1*np.sin(2*np.pi*900*t)

    def test_snr_calibration_and_one_shared_gain_even_when_noise_would_clip(self):
        source=self.source()
        interference=np.random.default_rng(22).normal(size=len(source))
        for requested in (-20,-5,0,5,20):
            clean,scaled,mix,meta=noise.mix_at_snr(source,interference,requested)
            self.assertAlmostEqual(meta['actual_snr_db'],requested,places=10)
            self.assertLessEqual(np.max(np.abs(mix)),.9500000001)
            np.testing.assert_allclose(clean+scaled,mix,rtol=0,atol=0)
            np.testing.assert_allclose(clean,source*meta['common_gain'])

    def test_scale_invariance_does_not_hide_attenuation_or_reward_muting(self):
        source=self.source()
        mixed=source+.04*np.random.default_rng(11).normal(size=len(source))
        before=noise.si_sdr(source,mixed)
        quiet=noise.si_sdr(source,mixed*.1)
        self.assertAlmostEqual(before['si_sdr_db'],quiet['si_sdr_db'],places=9)
        self.assertAlmostEqual(quiet['projection_gain_db']-before['projection_gain_db'],-20,places=9)
        self.assertEqual(noise.si_sdr(source,np.zeros_like(source))['si_sdr_db'],-120)

    def test_invalid_silent_nonfinite_and_mismatched_inputs_are_rejected(self):
        for a,b in [([],[]),([0,0],[1,1]),([1,0],[0,0]),([1,2],[1]),([float('nan')],[1])]:
            with self.subTest(a=a,b=b), self.assertRaises(ValueError):
                noise.mix_at_snr(a,b,0)
        with self.assertRaises(ValueError):noise.si_sdr([0,0],[1,2])
        with self.assertRaises(ValueError):noise.apply_profile([1,2],'raw',8000)

    def test_generated_noises_are_reproducible_nonempty_and_differ_across_seeds(self):
        for kind in noise.PARAMETERS['noise_types']:
            first=noise.make_noise(kind,16000,16000,123)
            second=noise.make_noise(kind,16000,16000,123)
            third=noise.make_noise(kind,16000,16000,124)
            np.testing.assert_array_equal(first,second)
            self.assertFalse(np.array_equal(first,third),kind)
            self.assertAlmostEqual(noise.rms(first),1,places=10)
            self.assertAlmostEqual(float(np.mean(first)),0,places=10)
        with self.assertRaises(ValueError):noise.make_noise('unknown',100,16000,1)

    def test_identity_profile_is_exact_and_filters_keep_length_and_finiteness(self):
        source=self.source(3101)
        np.testing.assert_array_equal(noise.apply_profile(source,'raw'),source)
        for profile in noise.PARAMETERS['profiles']:
            result=noise.apply_profile(source,profile)
            self.assertEqual(result.shape,source.shape)
            self.assertTrue(np.isfinite(result).all())
        with self.assertRaises(ValueError):noise.apply_profile(source,'unknown')

    def test_highpass_attenuates_low_rumble_more_than_inband_speech_tone(self):
        t=np.arange(32000)/16000
        attenuation=[]
        for hz in (20,1000):
            tone=np.sin(2*np.pi*hz*t)
            filtered=noise.apply_profile(tone,'highpass80')
            attenuation.append(noise.rms(filtered[1000:])/noise.rms(tone[1000:]))
        self.assertLess(attenuation[0],.3)
        self.assertGreater(attenuation[1],.95)

    def test_notch_can_damage_speech_band_and_is_not_universally_beneficial(self):
        t=np.arange(32000)/16000
        tone=np.sin(2*np.pi*900*t)
        filtered=noise.apply_profile(tone,'notch900_q2')
        self.assertLess(noise.rms(filtered[2000:]),noise.rms(tone[2000:])*.01)
        self.assertLess(noise.si_sdr(self.source(),noise.apply_profile(self.source(),'notch900_q2'))['si_sdr_db'],20)

    def test_pcm_roundtrip_preserves_duration_format_and_refuses_clipping(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'signal.wav';source=self.source()
            noise.write_pcm16(path,source)
            result=noise.load_pcm16(path)
            self.assertEqual(len(result),len(source))
            np.testing.assert_allclose(result,source,atol=5e-5)
            with self.assertRaises(ValueError):noise.write_pcm16(path,np.array([1.01]))

    def test_development_and_validation_seeds_are_disjoint_and_params_fixed(self):
        self.assertFalse(set(noise.PARAMETERS['development_seeds']) & set(noise.PARAMETERS['validation_seeds']))
        self.assertTrue(noise.PARAMETERS['selection_rule']['validation_does_not_reselect'])
        self.assertTrue(noise.PARAMETERS['selection_rule']['production_default_requires_actual_asr_and_human_noise_validation'])


if __name__=='__main__':
    unittest.main()
