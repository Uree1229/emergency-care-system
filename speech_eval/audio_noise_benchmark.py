"""Reproducible synthetic-audio noise A/B screening, never an ASR accuracy score.

Uses local Korean TTS WAVs supplied by a source plan. No microphone, playback,
network service, patient database, STT engine or user recording is accessed.
Filtering happens offline and is not connected to browser SpeechRecognition.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import time
import wave

import numpy as np


PARAMETERS = {
    'sample_rate': 16000,
    'noise_types': ['siren_chirp', 'engine_rumble', 'wind_proxy', 'babble_proxy', 'impact_proxy'],
    'snr_db': [20, 10, 5, 0, -5],
    'development_seeds': [1101, 1102],
    'validation_seeds': [2101, 2102, 2103],
    'profiles': ['raw', 'highpass80', 'notch900_q2', 'stationary_gate'],
    'highpass_cutoff_hz': 80,
    'highpass_impulse_samples': 512,
    'notch_center_hz': 900,
    'notch_q': 2,
    'notch_impulse_samples': 1024,
    'calibration_silence_seconds': 0.4,
    'gate_noise_estimate_seconds': 0.30,
    'gate_fft_samples': 512,
    'gate_hop_samples': 128,
    'gate_min_amplitude_gain': 0.20,
    'gate_noise_power_multiplier': 1.2,
    'all_profile_common_peak_ceiling': 0.95,
    'selection_rule': {
        'development_mean_si_sdr_gain_min_db': 0.5,
        'development_worst_noise_group_mean_gain_min_db': -0.5,
        'development_clean_min_si_sdr_db': 20.0,
        'development_clean_min_projection_gain_db': -1.0,
        'tie_break': 'profile order in PARAMETERS',
        'validation_does_not_reselect': True,
        'production_default_requires_actual_asr_and_human_noise_validation': True,
    },
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def finite_mono(value):
    result = np.asarray(value, dtype=np.float64)
    if result.ndim != 1 or not len(result) or not np.all(np.isfinite(result)):
        raise ValueError('A nonempty finite mono signal is required')
    return result


def rms(value):
    value = finite_mono(value)
    return float(np.sqrt(np.mean(value * value)))


def db_ratio(numerator, denominator):
    return float(np.clip(10 * np.log10(max(float(numerator), 1e-30) /
                                     max(float(denominator), 1e-30)), -120, 120))


def si_sdr(reference, output):
    """Scale-invariant, mean-centered waveform fidelity; silence is not rewarded."""
    reference, output = finite_mono(reference), finite_mono(output)
    if reference.shape != output.shape:
        raise ValueError('Reference and output lengths must match')
    source = reference - np.mean(reference)
    candidate = output - np.mean(output)
    energy = float(np.dot(source, source))
    if energy <= 1e-20:
        raise ValueError('A non-silent reference is required')
    if float(np.dot(candidate, candidate)) <= 1e-20:
        return {'si_sdr_db': -120.0, 'projection_gain_db': -120.0,
                'projection_gain': 0.0, 'output_rms': 0.0}
    gain = float(np.dot(candidate, source) / energy)
    target = gain * source
    residual = candidate - target
    return {'si_sdr_db': db_ratio(np.dot(target, target), np.dot(residual, residual)),
            'projection_gain_db': float(np.clip(20 * np.log10(max(abs(gain), 1e-12)), -120, 120)),
            'projection_gain': gain, 'output_rms': rms(candidate)}


def mix_at_snr(clean, noise, snr_db, peak=0.95):
    """One shared gain scales source and noise; no clipping or per-profile normalization."""
    clean, noise = finite_mono(clean), finite_mono(noise)
    if clean.shape != noise.shape or not math.isfinite(snr_db) or not 0 < peak < 1:
        raise ValueError('Equal signal lengths, finite SNR and a peak in (0,1) are required')
    if rms(clean) <= 1e-12 or rms(noise) <= 1e-12:
        raise ValueError('Both clean signal and noise must have energy')
    scaled_noise = noise * rms(clean) / (10 ** (snr_db / 20) * rms(noise))
    mixture = clean + scaled_noise
    gain = min(1.0, peak / max(float(np.max(np.abs(mixture))), 1e-12))
    source, interference = clean * gain, scaled_noise * gain
    result = source + interference
    actual_snr = db_ratio(np.dot(source, source), np.dot(interference, interference))
    return source, interference, result, {'common_gain': gain, 'actual_snr_db': actual_snr,
                                          'peak': float(np.max(np.abs(result)))}


def _spectral_shape(values, sample_rate, response):
    frequencies = np.fft.rfftfreq(len(values), 1 / sample_rate)
    return np.fft.irfft(np.fft.rfft(values) * response(frequencies), n=len(values))


def make_noise(kind, length, sample_rate, seed):
    if type(length) is not int or length <= 0 or type(sample_rate) is not int or sample_rate < 4000:
        raise ValueError('Positive length and sample rate >=4000 are required')
    generator = np.random.default_rng(seed)
    t = np.arange(length) / sample_rate
    if kind == 'siren_chirp':
        sweep = 1 - np.abs(2 * ((t * 0.8 + generator.uniform()) % 1) - 1)
        phase = 2 * np.pi * np.cumsum(600 + 1000 * sweep) / sample_rate
        values = (np.sin(phase) + 0.3 * np.sin(2 * phase)) * (0.8 + 0.2 * np.sin(2 * np.pi * 0.4 * t))
    elif kind == 'engine_rumble':
        values = sum(np.sin(2 * np.pi * frequency * t + generator.uniform(0, 2*np.pi)) / (index+1)
                     for index, frequency in enumerate((55, 110, 165, 220)))
        values = values * (0.8 + 0.2*np.sin(2*np.pi*1.1*t))
        values += 0.35 * _spectral_shape(generator.normal(size=length), sample_rate,
                                        lambda f: 1 / np.sqrt(1 + (f/180)**4))
    elif kind == 'wind_proxy':
        values = _spectral_shape(generator.normal(size=length), sample_rate,
                                 lambda f: 1 / np.sqrt(1 + (f/100)**4))
        values *= (0.2 + 0.8 * np.sin(2*np.pi*0.43*t + generator.uniform())**2)
    elif kind == 'babble_proxy':
        # Competing harmonic/formant envelopes, not recorded people or intelligible speech.
        values = np.zeros(length)
        for _ in range(4):
            fundamental = generator.uniform(95, 220)
            component = sum(np.sin(2*np.pi*fundamental*k*t + generator.uniform(0, 2*np.pi)) / k
                            for k in range(1, 20))
            component = _spectral_shape(component, sample_rate, lambda f:
                np.exp(-((f-600)/350)**2) + 0.6*np.exp(-((f-1500)/500)**2)
                + 0.3*np.exp(-((f-2600)/650)**2))
            component *= np.maximum(0, np.sin(2*np.pi*generator.uniform(2, 4)*t + generator.uniform(0, 5)))
            values += component
    elif kind == 'impact_proxy':
        values = 0.03 * generator.normal(size=length)
        for center in generator.integers(0, length, size=max(3, int(length/sample_rate))):
            span = min(length-int(center), int(sample_rate*0.14))
            values[center:center+span] += (generator.normal(size=span)
                * np.exp(-np.arange(span)/(sample_rate*0.018)))
    else:
        raise ValueError('Unknown noise type')
    values -= np.mean(values)
    if rms(values) <= 1e-12:
        raise ValueError('Generated noise is silent')
    return values / rms(values)


def _fft_convolve(signal, impulse):
    length = len(signal) + len(impulse) - 1
    fft_length = 1 << (length-1).bit_length()
    return np.fft.irfft(np.fft.rfft(signal, fft_length) * np.fft.rfft(impulse, fft_length),
                        fft_length)[:len(signal)]


def _highpass_impulse(sample_rate):
    alpha = 1 / (1 + 2*np.pi*PARAMETERS['highpass_cutoff_hz']/sample_rate)
    impulse = -(1-alpha) * alpha**np.arange(PARAMETERS['highpass_impulse_samples'])
    impulse[0] = alpha
    return impulse


def _notch_impulse(sample_rate):
    omega = 2*np.pi*PARAMETERS['notch_center_hz']/sample_rate
    alpha = np.sin(omega)/(2*PARAMETERS['notch_q'])
    b = np.array([1, -2*np.cos(omega), 1])/(1+alpha)
    a = np.array([1, -2*np.cos(omega)/(1+alpha), (1-alpha)/(1+alpha)])
    impulse = np.zeros(PARAMETERS['notch_impulse_samples'])
    for i in range(len(impulse)):
        impulse[i] = (b[i] if i < 3 else 0) - sum(a[k]*impulse[i-k] for k in (1,2) if i >= k)
    return impulse


def _stationary_gate(signal, sample_rate):
    nfft, hop = PARAMETERS['gate_fft_samples'], PARAMETERS['gate_hop_samples']
    pad = nfft//2
    padded = np.pad(signal, (pad, pad+nfft))
    frames = np.lib.stride_tricks.sliding_window_view(padded, nfft)[::hop].copy()
    window = np.hanning(nfft)
    spectrum = np.fft.rfft(frames*window, axis=1)
    power = np.abs(spectrum)**2
    count = max(1, min(len(frames), int(PARAMETERS['gate_noise_estimate_seconds']*sample_rate/hop)))
    noise_power = np.mean(power[:count], axis=0)
    gain = np.clip(1 - PARAMETERS['gate_noise_power_multiplier']*noise_power/(power+1e-16),
                   PARAMETERS['gate_min_amplitude_gain'], 1)
    # One frame of lookahead: this is an offline candidate, not live UI processing.
    gain = 0.5*gain + 0.25*np.vstack((gain[:1],gain[:-1])) + 0.25*np.vstack((gain[1:],gain[-1:]))
    restored = np.fft.irfft(spectrum*gain, nfft, axis=1)*window
    output, weights = np.zeros(len(padded)), np.zeros(len(padded))
    for index, frame in enumerate(restored):
        start = index*hop
        output[start:start+nfft] += frame
        weights[start:start+nfft] += window**2
    output /= np.maximum(weights, 1e-12)
    return output[pad:pad+len(signal)]


def apply_profile(signal, profile, sample_rate=16000):
    signal = finite_mono(signal)
    if sample_rate != PARAMETERS['sample_rate']:
        raise ValueError('This frozen experiment requires 16kHz input')
    if profile == 'raw':
        return signal.copy()
    if profile == 'highpass80':
        return _fft_convolve(signal, _highpass_impulse(sample_rate))
    if profile == 'notch900_q2':
        return _fft_convolve(signal, _notch_impulse(sample_rate))
    if profile == 'stationary_gate':
        return _stationary_gate(signal, sample_rate)
    raise ValueError('Unknown processing profile')


def load_pcm16(path):
    with wave.open(str(path), 'rb') as source:
        if (source.getframerate() != PARAMETERS['sample_rate'] or source.getnchannels() != 1
                or source.getsampwidth() != 2 or source.getcomptype() != 'NONE'):
            raise ValueError('Reference must be 16kHz mono 16-bit PCM WAV')
        return finite_mono(np.frombuffer(source.readframes(source.getnframes()), dtype='<i2') / 32768)


def write_pcm16(path, signal):
    signal = finite_mono(signal)
    if np.max(np.abs(signal)) > 1:
        raise ValueError('Refusing to silently clip a benchmark WAV')
    with wave.open(str(path), 'wb') as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(PARAMETERS['sample_rate'])
        destination.writeframes(np.round(signal*32767).astype('<i2').tobytes())


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['profile']].append(row)
    result = {}
    for profile, selected in groups.items():
        noise_groups = {}
        for noise in PARAMETERS['noise_types']:
            group = [r for r in selected if r['noise'] == noise]
            noise_groups[noise] = {'cases':len(group),
                'mean_si_sdr_gain_db':float(np.mean([r['si_sdr_gain_db'] for r in group])),
                'worse_than_raw':sum(r['si_sdr_gain_db'] < -0.1 for r in group),
                'worst_gain_db':min(r['si_sdr_gain_db'] for r in group)}
        result[profile] = {'cases':len(selected),
            'mean_si_sdr_gain_db':float(np.mean([r['si_sdr_gain_db'] for r in selected])),
            'median_si_sdr_gain_db':float(np.median([r['si_sdr_gain_db'] for r in selected])),
            'worst_gain_db':min(r['si_sdr_gain_db'] for r in selected),
            'worse_than_raw':sum(r['si_sdr_gain_db'] < -0.1 for r in selected),
            'by_noise':noise_groups}
    return result


def benchmark(source_plan, clean_directory, output):
    source_plan, clean_directory, output = map(lambda p:Path(p).resolve(), (source_plan,clean_directory,output))
    if output.exists():
        raise FileExistsError('Refusing to overwrite an existing experiment output')
    plan = json.loads(source_plan.read_text(encoding='utf-8-sig'))
    if plan.get('source') != 'local_windows_tts' or plan.get('human_voice') is not False:
        raise ValueError('This experiment is scoped to explicitly labeled local synthetic TTS')
    source_rows, clean = [], {}
    for reference in plan['references']:
        path = clean_directory/(reference['id']+'.wav')
        samples = load_pcm16(path)
        clean[reference['id']] = np.concatenate((np.zeros(int(PARAMETERS['sample_rate']*
            PARAMETERS['calibration_silence_seconds'])), samples))
        source_rows.append({'id':reference['id'],'partition':reference['partition'],
                            'text':reference['text'],'wav':str(path),'sha256':sha256(path),
                            'samples':len(samples)})
    for partition in ('development','validation'):
        if not any(row['partition'] == partition for row in source_rows):
            raise ValueError('Both partitions must be independently assigned before evaluation')
    output.mkdir(parents=True)
    samples_dir = output/'samples'
    samples_dir.mkdir()
    frozen = {'scope':'Synthetic Korean TTS + generated noise; waveform screening, not ASR or field accuracy',
              'parameters':PARAMETERS,'sources':source_rows,'code_sha256':sha256(__file__),
              'source_plan_sha256':sha256(source_plan),'actual_human_voice_count':0,
              'actual_stt_calls':0,'actual_microphone_calls':0,
              'noise_model_limitations':'Synthetic chirp/rumble/gust/formant/impact proxies, not recordings from an ambulance',
              'calibration_assumption':'Stationary gate receives a known 0.4-second noise-only prefix. Real field capture may not have it.',
              'noise_measurement':'SNR calibrated over the full clip including silence; paired clean reference kept for every mixture',
              'safety':'No per-output normalization; all source/noise/processed versions get one shared peak gain'}
    write_json(output/'frozen-plan.json',frozen)
    frozen_hash = sha256(output/'frozen-plan.json')
    clean_metrics = []
    for reference in source_rows:
        source = clean[reference['id']]
        for profile in PARAMETERS['profiles']:
            clean_metrics.append({'id':reference['id'],'partition':reference['partition'],
                                  'profile':profile,**si_sdr(source,apply_profile(source,profile))})
    write_json(output/'clean-retention.json',clean_metrics)
    rows, manifest = [], []
    started = time.perf_counter()
    selection = None
    for partition in ('development','validation'):
        if partition == 'validation':
            dev_summary = summarize(rows)
            eligible = []
            rule = PARAMETERS['selection_rule']
            for profile in PARAMETERS['profiles'][1:]:
                metrics = dev_summary[profile]
                clean_group = [r for r in clean_metrics if r['profile']==profile and r['partition']=='development']
                if (metrics['mean_si_sdr_gain_db'] >= rule['development_mean_si_sdr_gain_min_db']
                    and min(g['mean_si_sdr_gain_db'] for g in metrics['by_noise'].values()) >= rule['development_worst_noise_group_mean_gain_min_db']
                    and min(r['si_sdr_db'] for r in clean_group) >= rule['development_clean_min_si_sdr_db']
                    and min(r['projection_gain_db'] for r in clean_group) >= rule['development_clean_min_projection_gain_db']):
                    eligible.append(profile)
            selected = max(eligible, key=lambda p:dev_summary[p]['mean_si_sdr_gain_db']) if eligible else 'raw'
            selection = {'selected_on_development':selected,'eligible':eligible,
                         'frozen_plan_sha256':frozen_hash,'validation_reselection_allowed':False,
                         'development_summary':dev_summary}
            write_json(output/'development-selection.json',selection)
            selection_hash = sha256(output/'development-selection.json')
            write_json(output/'before-validation.json',{'plan_sha256':frozen_hash,'selection_sha256':selection_hash})
        selected_sources = [r for r in source_rows if r['partition']==partition]
        for reference in selected_sources:
            for noise_index, noise in enumerate(PARAMETERS['noise_types']):
                for snr in PARAMETERS['snr_db']:
                    for seed in PARAMETERS[partition+'_seeds']:
                        source = clean[reference['id']]
                        noise_signal = make_noise(noise,len(source),PARAMETERS['sample_rate'],seed+noise_index*100000)
                        target, interference, mixed, mix_info = mix_at_snr(source,noise_signal,snr)
                        processed = {profile:apply_profile(mixed,profile) for profile in PARAMETERS['profiles']}
                        common_gain = min(1.0, PARAMETERS['all_profile_common_peak_ceiling'] /
                            max(float(np.max(np.abs(values))) for values in processed.values()))
                        target *= common_gain
                        interference *= common_gain
                        processed = {key:value*common_gain for key,value in processed.items()}
                        baseline = si_sdr(target,processed['raw'])['si_sdr_db']
                        case_id = f"{reference['id']}-{noise}-snr{snr}-seed{seed}"
                        for profile, values in processed.items():
                            metrics = si_sdr(target,values)
                            rows.append({'id':case_id,'reference':reference['id'],'partition':partition,
                                'noise':noise,'target_snr_db':snr,'seed':seed,'profile':profile,
                                'actual_snr_db':mix_info['actual_snr_db'],
                                'common_gain':mix_info['common_gain']*common_gain,
                                **metrics,'si_sdr_gain_db':metrics['si_sdr_db']-baseline,
                                'peak':float(np.max(np.abs(values)))})
                        representative = (partition=='validation' and seed==2101 and
                            ((reference['id']=='T004' and snr==5) or
                             (reference['id']=='T005' and noise=='siren_chirp' and snr==0)))
                        if representative:
                            versions = {'reference':target,'noise_only':interference,**processed}
                            files = []
                            for version, values in versions.items():
                                path=samples_dir/(case_id+'-'+version+'.wav')
                                write_pcm16(path,values)
                                files.append({'version':version,'path':str(path),'sha256':sha256(path)})
                            manifest.append({'case_id':case_id,'source_reference':reference['id'],
                                'noise':noise,'snr_db':snr,'seed':seed,
                                'shared_gain':mix_info['common_gain']*common_gain,'files':files})
    noise_only_files = []
    for index, noise in enumerate(PARAMETERS['noise_types']):
        values = make_noise(noise,16000*4,16000,9001+index)
        values *= min(0.1, 0.9/np.max(np.abs(values)))
        path = samples_dir/('noise-only-'+noise+'.wav')
        write_pcm16(path,values)
        noise_only_files.append({'noise':noise,'path':str(path),'sha256':sha256(path),
                                 'contains_reference_speech':False,'ASR_hallucination_measured':False})
    write_json(output/'paired-sample-manifest.json',{'mixtures':manifest,'noise_only':noise_only_files})
    with (output/'measurements.jsonl').open('w',encoding='utf-8') as result_file:
        for row in rows:
            result_file.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    val_rows=[r for r in rows if r['partition']=='validation']
    summary={'scope':frozen['scope'],'tts_voice':plan['voice'],'reference_count':len(source_rows),
        'mixtures':len(rows)//len(PARAMETERS['profiles']),'profile_evaluations':len(rows),
        'development_mixtures':sum(r['partition']=='development' for r in rows)//len(PARAMETERS['profiles']),
        'validation_mixtures':len(val_rows)//len(PARAMETERS['profiles']),
        'validation':summarize(val_rows),'selected_on_development':selection['selected_on_development'],
        'production_default_adopted':False,
        'adoption_reason':'No actual ASR or human field-noise validation; signal-only screening cannot justify a production default.',
        'asr_CER_WER':None,'actual_human_voice_count':0,'actual_STT_calls':0,
        'noise_suppression_browser_flag_tested':False,
        'source_stable':sha256(__file__)==frozen['code_sha256'] and sha256(source_plan)==frozen['source_plan_sha256']
            and all(sha256(r['wav'])==r['sha256'] for r in source_rows),
        'frozen_plan_stable':sha256(output/'frozen-plan.json')==frozen_hash,
        'development_selection_stable':sha256(output/'development-selection.json')==selection_hash,
        'max_snr_calibration_error_db':max(abs(r['actual_snr_db']-r['target_snr_db']) for r in rows),
        'all_output_finite_and_unclipped':all(math.isfinite(r['si_sdr_db']) and r['peak']<=0.95000001 for r in rows),
        'elapsed_seconds':round(time.perf_counter()-started,3),
        'representative_mixtures':len(manifest),'noise_only_samples':len(noise_only_files)}
    write_json(output/'summary.json',summary)
    lines=['합성 한국어 음성 + 생성 잡음 A/B 신호 시험','',
        '실제 현장 녹음·마이크·STT 정확도 시험이 아니다. Windows 로컬 TTS 한 목소리와 합성 잡음만 사용했다.',
        '같은 깨끗한 원음/잡음/공통 gain으로 비교했다. 출력별 음량 정규화는 하지 않았다.',
        'stationary_gate는 앞 0.4초에 말하지 않고 잡음만 받는 가정이 있다. 실제 현장에서 이 가정이 성립하는지 미검증이다.',
        f"혼합 음원 {summary['mixtures']}개 / 프로파일 평가 {summary['profile_evaluations']}개 / 검증 혼합 {summary['validation_mixtures']}개",'',
        '검증 집계: 프로파일 | 평균 SI-SDR 변화(dB) | 0.1dB 초과 악화 수 | 최악 변화(dB)']
    for profile, metrics in summary['validation'].items():
        lines.append(f"{profile}: {metrics['mean_si_sdr_gain_db']:+.3f} | {metrics['worse_than_raw']}/{metrics['cases']} | {metrics['worst_gain_db']:+.3f}")
    lines += ['',f"개발 자료에서 고정한 선택: {summary['selected_on_development']}",
              '프로덕션 기본 소음 처리 채택: 없음. 실제 STT의 숫자·부정 오류와 인식률 검증이 필요하다.',
              'SI-SDR은 파형 지표이며 CER/WER·말의 이해도·임상 정확도와 같지 않다.',
              '소음별 악화와 clean-retention.json의 깨끗한 음성 손상도 함께 확인해야 한다.']
    (output/'report.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-plan',required=True,type=Path)
    parser.add_argument('--clean-directory',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    result=benchmark(args.source_plan,args.clean_directory,args.output)
    return 0 if result['source_stable'] and result['frozen_plan_stable'] and result['all_output_finite_and_unclipped'] else 1


if __name__=='__main__':
    raise SystemExit(main())
