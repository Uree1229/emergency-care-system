/** One local recording, one shared PCM input, two explicitly labelled paths. No STT/API writes. */
import {createNoiseSuppressor,encodeMonoWav} from './noise-suppression.mjs';
import {makePracticeAudioStore,PracticeAudioCapture} from './practice-audio.mjs';
import {findVoiceTrigger} from './recording-control.mjs';

const $=id=>document.getElementById('noise-'+id);
const LIMITS=Object.freeze({inputBytes:20*1024*1024,durationSeconds:180,channels:8,sampleRate:48000,archiveBytes:96*1024*1024,text:16000});
const encoder=new TextEncoder(),audioStore=makePracticeAudioStore();
const urls=new Set(),playerURLs=new Map();
let input=null,result=null,job=null,selection=0,libraryGeneration=0,lifecycleGeneration=0,libraryRows=[],libraryLoading=false,selecting=false,exporting=false,closed=false,micOwner=0,micBusy=false,micStopping=false,micStarted=false,micAttemptId=null;
const audioCapture=new PracticeAudioCapture({onLimit:()=>{void stopMicrophone('limit');},onState:(state,info)=>{
  if(closed||!micBusy||info?.attemptId!==micAttemptId)return;
  if(state==='recording')micStarted=true;
  if(micStarted&&!micStopping&&['ready','failed','empty','aborted','unavailable'].includes(state))void stopMicrophone('unexpected_end');
}});
const clone=value=>JSON.parse(JSON.stringify(value));
const failure=(code,message)=>Object.assign(new Error(message),{code});
function error(message=''){$('error').textContent=message;$('error').hidden=!message;}
function status(message){$('status').textContent=message;}
function cancelled(){return failure('ABORTED','처리를 취소했습니다.');}
function current(owner){return !closed&&job===owner&&input===owner.input&&!owner.controller.signal.aborted;}
function assertCurrent(owner){if(!current(owner))throw cancelled();}
function controls(){
  const recording=micBusy||audioCapture.active,blocked=!!job||recording||selecting||exporting;
  $('process').disabled=!input||blocked;$('cancel').disabled=!job;
  $('file').disabled=recording;$('practice-refresh').disabled=recording||libraryLoading;
  $('practice-select').disabled=recording||!libraryRows.length;$('practice-load').disabled=recording||!libraryRows.length||!$('practice-select').value;
  $('record').disabled=blocked;$('record-stop').disabled=!recording||micStopping;$('record-cancel').disabled=!recording;
  $('original-download').disabled=!input||recording;
  for(const id of ['raw-download','processed-download','json','zip','compare-text','clear'])$(id).disabled=!result||blocked;
  for(const id of ['pair','speaker','condition','reference','notes','raw-transcript','processed-transcript'])$(id).disabled=exporting;
  $('duration-override').disabled=blocked;
}
function pausePlayers(){for(const id of ['original-player','raw-player','processed-player']){try{$(id).pause();}catch{}}}
function revokePlayer(id){const old=playerURLs.get(id);if(old){URL.revokeObjectURL(old);urls.delete(old);playerURLs.delete(id);}$(id).removeAttribute('src');$(id).load?.();}
function setPlayer(id,blob){revokePlayer(id);const url=URL.createObjectURL(blob);urls.add(url);playerURLs.set(id,url);$(id).src=url;}
function clearResult(){pausePlayers();for(const id of ['raw-player','processed-player'])revokePlayer(id);result=null;$('results').hidden=true;for(const id of ['raw-transcript','processed-transcript'])$(id).value='';$('text-result').textContent='인식문을 넣으면 공통 참조문과 명령 판정을 비교할 수 있습니다.';}
function cancelProcessing(message='처리를 취소했습니다. 원본 입력은 유지합니다.'){
  const previous=job;job=null;previous?.controller.abort();try{previous?.processor?.dispose();}catch{}try{Promise.resolve(previous?.decodeContext?.close?.()).catch(()=>{});}catch{}if(previous&&!closed)status(message+' 브라우저 디코딩이 늦게 끝나도 취소한 결과는 적용하지 않습니다.');controls();
}
function clearInput(){selection++;selecting=false;cancelProcessing();clearResult();revokePlayer('original-player');$('original-player').hidden=true;input=null;$('source').textContent='선택한 입력 없음';controls();}
async function sha256(value){
  if(typeof crypto?.subtle?.digest!=='function')throw failure('HASH_UNAVAILABLE','SHA-256을 사용할 수 없습니다. HTTPS 또는 localhost에서 여세요.');
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256',value))].map(byte=>byte.toString(16).padStart(2,'0')).join('');
}
function extensionFor(blob){return ({'audio/webm':'webm','audio/ogg':'ogg','audio/mp4':'mp4','audio/x-m4a':'m4a','audio/wav':'wav','audio/x-wav':'wav','audio/wave':'wav','audio/mpeg':'mp3','audio/aac':'aac','audio/flac':'flac'})[(blob.type||'').split(';')[0].trim().toLowerCase()]||'bin';}
function sourceLabel(kind){return ({file:'선택한 파일',practice:'연습 보관함의 녹음',microphone:'이번에 한 번 녹음한 파일'})[kind]||'선택한 입력';}
async function selectInput(blob,{kind='file',reference='',warning='',expectedMicOwner=null}={}){
  if(closed||expectedMicOwner!==null&&micOwner!==expectedMicOwner)return false;
  clearInput();const token=selection;selecting=true;error();status('원본 바이트와 SHA-256을 확인합니다.');controls();
  try{
    if(!blob||typeof blob.arrayBuffer!=='function'||!Number.isSafeInteger(blob.size)||blob.size<=0)throw failure('EMPTY_INPUT','비어 있지 않은 음성 파일을 선택하세요.');
    if(blob.size>LIMITS.inputBytes)throw failure('INPUT_LIMIT','입력 파일은 최대 20MiB입니다. 원본 파일은 변경하지 않았습니다.');
    const bytes=new Uint8Array(await blob.arrayBuffer());if(bytes.byteLength!==blob.size)throw failure('INPUT_CHANGED','원본 파일의 크기를 확인하지 못했습니다.');
    const hash=await sha256(bytes);if(closed||selection!==token||expectedMicOwner!==null&&micOwner!==expectedMicOwner)return false;
    input={blob,bytes,sha256:hash,kind,extension:extensionFor(blob),warning:String(warning||'').slice(0,80)};
    setPlayer('original-player',blob);$('original-player').hidden=false;
    $('source').textContent=`${sourceLabel(kind)} · ${(blob.size/1024/1024).toFixed(2)}MiB\nSHA-256 ${hash}${input.warning?'\n부분 녹음·입력 경고: '+input.warning:''}`;
    if(reference&&!$('reference').value){$('reference').value=String(reference).slice(0,LIMITS.text);$('reference-status').textContent='선택한 연습 시도의 참조문을 불러왔습니다. 실제로 말한 내용과 같은지 다시 확인하세요.';}
    else if($('reference').value)$('reference-status').textContent='이전 공통 참조문을 덮어쓰지 않고 유지했습니다. 새로 선택한 녹음에도 맞는지 확인하세요.';
    status('입력을 확인했습니다. 참조문이 이 녹음과 맞는지 확인한 뒤 A/B 만들기를 누르세요.');return true;
  }catch(err){if(!closed&&selection===token){error(err.message||'원본 파일을 읽지 못했습니다.');status('입력을 선택하지 못했습니다. 기존 원본 파일과 연습 보관함은 수정하지 않았습니다.');}return false;}
  finally{if(selection===token){selecting=false;controls();}}
}
async function refreshPractice(){
  const token=++libraryGeneration;libraryLoading=true;error();controls();
  try{const rows=await audioStore.list();if(closed||token!==libraryGeneration)return;
    libraryRows=rows.sort((a,b)=>(b.descriptor.savedAt||0)-(a.descriptor.savedAt||0));$('practice-select').replaceChildren();
    const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=rows.length?'녹음을 선택하세요':'보관된 연습 녹음 없음';$('practice-select').append(placeholder);
    libraryRows.forEach((row,index)=>{const option=document.createElement('option');option.value=String(index+1);option.textContent=`${row.snapshot.caseId||'자유 발화'} · ${new Date(row.descriptor.savedAt).toLocaleString()} · ${(row.blob.size/1024).toFixed(0)}KiB${row.descriptor.captureErrorCode?' · 부분 녹음 경고':''}`;$('practice-select').append(option);});
    $('practice-select').value='';status(`보관함에서 검증한 녹음 ${rows.length}개를 읽었습니다. 기존 자료는 바꾸지 않았습니다.`);
  }catch(err){if(!closed&&token===libraryGeneration)error('연습 녹음 보관함을 읽지 못했습니다. '+(err.message||''));}
  finally{if(token===libraryGeneration){libraryLoading=false;controls();}}
}
async function loadPractice(){const row=libraryRows[Number($('practice-select').value)-1];if(!row||micBusy)return false;return selectInput(row.blob,{kind:'practice',reference:row.snapshot.referenceText,warning:row.descriptor.captureErrorCode});}
async function startMicrophone(){
  if(closed||micBusy||job||selecting||exporting)return false;clearInput();error();const owner=++micOwner;micBusy=true;micStopping=false;micStarted=false;micAttemptId='NOISE-'+Date.now()+'-'+Math.random().toString(16).slice(2);controls();status('녹음 마이크 권한을 확인합니다. 음성 인식은 실행하지 않습니다.');
  try{const started=await audioCapture.start(micAttemptId);if(closed||owner!==micOwner)return false;
    if(started.status!=='recording')throw failure(started.errorCode||'AUDIO_START_FAILED','녹음을 시작하지 못했습니다. 마이크 권한·코덱 지원을 확인하세요.');
    status('원본 한 개를 녹음 중입니다. 끝나면 녹음 마치기를 누르세요. STT는 실행하지 않습니다.');return true;
  }catch(err){if(!closed&&owner===micOwner){micBusy=false;error(err.message||'녹음을 시작하지 못했습니다.');}return false;}
  finally{controls();}
}
async function stopMicrophone(reason='button'){
  if(!micBusy||micStopping)return false;const owner=micOwner;micStopping=true;controls();
  try{const captured=await audioCapture.stop({reason});if(closed||owner!==micOwner)return false;micBusy=false;micStopping=false;micStarted=false;micAttemptId=null;
    if(!captured.blob?.size)throw failure(captured.errorCode||'AUDIO_EMPTY','확보한 녹음이 없습니다. 권한·장치 상태를 확인하고 다시 녹음하세요.');
    const selected=await selectInput(captured.blob,{kind:'microphone',warning:captured.errorCode||(reason==='unexpected_end'?'AUDIO_ENDED_UNEXPECTEDLY':''),expectedMicOwner:owner});
    if(selected&&reason==='unexpected_end'&&!closed&&owner===micOwner)status('마이크나 녹음기가 먼저 종료됐습니다. 확보한 원음을 변경 없이 유지했습니다. 부분 녹음 경고를 확인하고 직접 재생하세요.');return selected;
  }catch(err){if(!closed&&owner===micOwner){micBusy=false;micStopping=false;error(err.message||'녹음을 마치지 못했습니다.');}return false;}
  finally{controls();}
}
function cancelMicrophone(){micOwner++;micBusy=false;micStopping=false;micStarted=false;micAttemptId=null;audioCapture.cancel();status('이번 녹음을 취소했습니다. 보관함과 서버에는 저장하지 않았습니다.');controls();}
async function preflightDuration(owner){
  const media=document.createElement('audio'),url=URL.createObjectURL(owner.input.blob);urls.add(url);
  return new Promise((resolve,reject)=>{
    let done=false,timer=null;const cleanup=()=>{if(timer!==null)clearTimeout(timer);owner.controller.signal.removeEventListener('abort',abort);media.onloadedmetadata=media.onerror=null;media.removeAttribute('src');try{media.load?.();}catch{}URL.revokeObjectURL(url);urls.delete(url);};
    const settle=(value,err=null)=>{if(done)return;done=true;cleanup();if(err)reject(err);else resolve(value);};
    const unknown=()=>{if(owner.allowUnknownDuration)settle({status:'unknown_explicitly_allowed',seconds:null,method:'html_media_metadata',timeoutMs:5000});else settle(null,failure('DURATION_UNKNOWN','음성 길이를 먼저 확인하지 못했습니다. 길이를 아는 짧은 파일을 사용하거나, 디코딩 메모리 사용 안내를 읽고 길이 미확인 허용을 선택하세요.'));};
    const abort=()=>settle(null,cancelled());owner.controller.signal.addEventListener('abort',abort,{once:true});
    media.onloadedmetadata=()=>{const seconds=media.duration;if(!Number.isFinite(seconds)||seconds<=0){unknown();return;}if(seconds>LIMITS.durationSeconds)settle(null,failure('DURATION_LIMIT','입력은 최대 180초입니다. 긴 파일을 디코딩하거나 자르지 않았습니다.'));else settle({status:'known',seconds,method:'html_media_metadata',timeoutMs:5000});};media.onerror=unknown;
    timer=setTimeout(unknown,5000);media.preload='metadata';media.src=url;try{media.load();}catch{unknown();}
  });
}
async function decodeSharedPCM(owner){
  const Audio=window.AudioContext||window.webkitAudioContext;if(typeof Audio!=='function')throw failure('DECODE_UNAVAILABLE','이 브라우저에서는 음성 파일을 디코딩할 수 없습니다.');
  let context;try{try{context=new Audio({sampleRate:LIMITS.sampleRate});}catch{context=new Audio();}owner.decodeContext=context;const buffer=await context.decodeAudioData(owner.input.bytes.buffer.slice(0));assertCurrent(owner);
    if(!Number.isInteger(buffer.length)||buffer.length<=0||!Number.isFinite(buffer.sampleRate)||buffer.sampleRate<8000||buffer.sampleRate>192000||buffer.numberOfChannels<1||buffer.numberOfChannels>LIMITS.channels)throw failure('AUDIO_FORMAT','디코딩한 음성 형식·채널 수를 확인하세요. 최대 8채널입니다.');
    const duration=buffer.length/buffer.sampleRate;if(duration>LIMITS.durationSeconds)throw failure('DURATION_LIMIT','비교할 음성은 최대 180초입니다. 원본 파일은 변경하지 않았습니다.');
    const mono=new Float32Array(buffer.length);let clippedInputSamples=0;
    for(let channel=0;channel<buffer.numberOfChannels;channel++){const samples=buffer.getChannelData(channel);for(let i=0;i<mono.length;i++){if(!Number.isFinite(samples[i]))throw failure('INVALID_PCM','유효하지 않은 음성 샘플이 있습니다.');mono[i]+=samples[i]/buffer.numberOfChannels;}}
    let pcm=mono;
    if(buffer.sampleRate!==LIMITS.sampleRate){
      const Offline=window.OfflineAudioContext||window.webkitOfflineAudioContext;if(typeof Offline!=='function')throw failure('RESAMPLE_UNAVAILABLE','48kHz 공통 변환을 지원하지 않는 브라우저입니다.');
      const offline=new Offline(1,Math.max(1,Math.round(duration*LIMITS.sampleRate)),LIMITS.sampleRate),source=offline.createBufferSource(),audio=offline.createBuffer(1,mono.length,buffer.sampleRate);audio.getChannelData(0).set(mono);source.buffer=audio;source.connect(offline.destination);source.start();const rendered=await offline.startRendering();assertCurrent(owner);pcm=new Float32Array(rendered.getChannelData(0));
    }
    for(let i=0;i<pcm.length;i++){if(!Number.isFinite(pcm[i]))throw failure('INVALID_PCM','공통 변환에서 유효하지 않은 샘플을 받았습니다.');if(pcm[i]>1||pcm[i]<-1){clippedInputSamples++;pcm[i]=Math.max(-1,Math.min(1,pcm[i]));}}
    if(pcm.length>LIMITS.durationSeconds*LIMITS.sampleRate)throw failure('DURATION_LIMIT','48kHz 변환 결과가 180초 한도를 넘었습니다.');
    return {pcm,conversion:{originalEncodedSampleRate:null,decodeContextSampleRate:context.sampleRate??null,decoderMayResample:true,decodedSampleRate:buffer.sampleRate,decodedChannels:buffer.numberOfChannels,channelMix:'arithmetic_mean',sampleRate:LIMITS.sampleRate,samples:pcm.length,durationSeconds:pcm.length/LIMITS.sampleRate,resampling:buffer.sampleRate===LIMITS.sampleRate?'browser_decode_at_context_rate_48000':'browser_decode_then_offline_audio_context_48000',amplitudePolicy:'shared_clamp_minus1_plus1_no_gain_normalization',clippedInputSamples}};
  }finally{try{await context?.close?.();}catch{}}
}
function signalMetrics(pcm){let sum=0,peak=0,clipped=0;for(const sample of pcm){if(!Number.isFinite(sample))throw failure('INVALID_OUTPUT','처리 결과에 유효하지 않은 샘플이 있습니다.');const magnitude=Math.abs(sample);sum+=sample*sample;peak=Math.max(peak,magnitude);if(magnitude>=1)clipped++;}return {samples:pcm.length,durationSeconds:pcm.length/LIMITS.sampleRate,rms:Math.sqrt(sum/pcm.length),peak,clippedSamples:clipped,meaning:'signal_characteristics_not_recognition_accuracy'};}
function showMetrics(value){return `길이 ${value.durationSeconds.toFixed(3)}초 · ${value.samples.toLocaleString()} samples\nRMS ${value.rms.toFixed(6)} · peak ${value.peak.toFixed(6)}\n|sample| ≥ 1: ${value.clippedSamples}개\n인식률·임상 정확도 수치가 아닙니다.`;}
async function processInput(){
  if(!input||job||micBusy||selecting||exporting||closed)return false;clearResult();error();const owner={input,controller:new AbortController(),processor:null,allowUnknownDuration:$('duration-override').checked};job=owner;status('재생 없이 입력 길이를 확인한 뒤 같은 입력을 48kHz 모노로 공통 변환합니다.');controls();
  try{const durationPreflight=await preflightDuration(owner);assertCurrent(owner);const {pcm,conversion}=await decodeSharedPCM(owner);assertCurrent(owner);const rawWav=encodeMonoWav(pcm,LIMITS.sampleRate);
    owner.processor=await createNoiseSuppressor({yieldEveryFrames:100});assertCurrent(owner);
    const processed=await owner.processor.process(pcm,{sampleRate:LIMITS.sampleRate,signal:owner.controller.signal,onProgress:progress=>{if(current(owner)){const percent=typeof progress==='number'?progress:progress?.ratio??progress?.fraction??progress?.progress;status(Number.isFinite(percent)?`RNNoise 처리 중 · ${Math.max(0,Math.min(100,Math.round(percent*100)))}%`:'RNNoise 처리 중 · 원본 입력을 보존합니다.');}}});assertCurrent(owner);
    if(!(processed?.pcm instanceof Float32Array)||processed.pcm.length!==pcm.length||!processed.wav||typeof processed.wav.arrayBuffer!=='function')throw failure('INVALID_OUTPUT','처리 결과와 원본의 샘플 길이가 일치하지 않습니다.');
    const rawBytes=new Uint8Array(await rawWav.arrayBuffer()),processedBytes=new Uint8Array(await processed.wav.arrayBuffer());
    const [rawHash,processedHash]=await Promise.all([sha256(rawBytes),sha256(processedBytes)]);assertCurrent(owner);
    const metrics={raw:signalMetrics(pcm),processed:signalMetrics(processed.pcm)};
    result={input:owner.input,rawWav,processedWav:processed.wav,rawBytes,processedBytes,base:{schema:1,purpose:'local_noise_ab_comparison',createdAt:new Date().toISOString(),sttAutomaticallyRun:false,originalBytesPreserved:true,source:{kind:owner.input.kind,contentType:owner.input.blob.type||'application/octet-stream',byteLength:owner.input.blob.size,sha256:owner.input.sha256,warning:owner.input.warning},durationPreflight,conversion,algorithm:clone(processed.descriptor),audio:{raw:{path:'raw-48k-mono.wav',contentType:'audio/wav',byteLength:rawBytes.length,sha256:rawHash},processed:{path:'rnnoise-48k-mono.wav',contentType:'audio/wav',byteLength:processedBytes.length,sha256:processedHash}},metrics,limits:LIMITS}};
    setPlayer('raw-player',rawWav);setPlayer('processed-player',processed.wav);$('raw-metrics').textContent=showMetrics(metrics.raw);$('processed-metrics').textContent=showMetrics(metrics.processed);$('details').textContent=JSON.stringify(result.base,null,2);$('results').hidden=false;
    status('같은 입력의 A/B를 만들었습니다. 직접 재생하고 ZIP 파일을 확인하세요. STT는 실행하지 않았습니다.');return true;
  }catch(err){if(current(owner)){error(err.message||'소음 처리를 완료하지 못했습니다.');status('비교 결과를 만들지 못했습니다. 선택한 원본 파일은 유지합니다.');}return false;}
  finally{try{owner.processor?.dispose();}catch{}if(job===owner){job=null;controls();}}
}
function boundedText(id,max){const value=$(id).value;if(typeof value!=='string'||value.length>max)throw failure('TEXT_LIMIT','비교 조건·원문의 길이 한도를 확인하세요.');return value;}
function annotation(){
  const pairId=boundedText('pair',80).trim(),speakerId=boundedText('speaker',80).trim();for(const value of [pairId,speakerId])if(value&&!/^[A-Za-z0-9][A-Za-z0-9._-]*$/u.test(value))throw failure('ANONYMOUS_ID','짝·화자 ID는 실명 대신 영문·숫자·점·밑줄·하이픈으로 입력하세요.');
  const referenceText=boundedText('reference',LIMITS.text),rawText=boundedText('raw-transcript',LIMITS.text),processedText=boundedText('processed-transcript',LIMITS.text),comparable=text=>text.trim().replace(/\s+/gu,' ');
  return {pairId,speakerId,condition:boundedText('condition',500),notes:boundedText('notes',2000),referenceText,comparison:{method:'manual_transcripts_shared_reference_current_command_parser',automaticSTT:false,referenceCommand:referenceText?findVoiceTrigger(referenceText):null,raw:{text:rawText,command:rawText?findVoiceTrigger(rawText):null,exactNormalizedMatch:referenceText&&rawText?comparable(referenceText)===comparable(rawText):null},processed:{text:processedText,command:processedText?findVoiceTrigger(processedText):null,exactNormalizedMatch:referenceText&&processedText?comparable(referenceText)===comparable(processedText):null}}};
}
function labelCommand(trigger){return trigger?`${({stop:'종료',recommend:'병원 추천',new_patient:'새 환자',previous_patient:'이전 환자',handoff_summary:'인계 요약'})[trigger.action]||trigger.action} · “${trigger.command}”`:'명령 없음';}
function compareText(){if(!result)return false;try{error();const data=annotation(),match=value=>value===null?'정답 또는 인식문 미입력':value?'공백 정리 후 완전 일치':'공백 정리 후 불일치';$('text-result').textContent=`공통 참조: ${data.referenceText?labelCommand(data.comparison.referenceCommand):'미입력'}\nA: ${labelCommand(data.comparison.raw.command)} · ${match(data.comparison.raw.exactNormalizedMatch)}\nB: ${labelCommand(data.comparison.processed.command)} · ${match(data.comparison.processed.exactNormalizedMatch)}\n수동으로 넣은 문장의 현재 판정입니다. 자동 STT·CER/WER·현장 정확도를 측정한 결과가 아닙니다.`;return true;}catch(err){error(err.message);return false;}}
function metadata(){if(!result)throw failure('NO_RESULT','먼저 A/B 비교를 만드세요.');return {...clone(result.base),annotations:annotation(),limitations:['No automatic STT; these processed samples are not injected into browser recognition.','Signal metrics are not recognition or clinical accuracy.','Decoded/mixed/resampled PCM differs from original encoded bytes; original source remains unchanged.','RNNoise may suppress speech and cannot guarantee removal of sirens or overlapping people.','Current comparison lives only in page memory; review all files for private information before sharing.']};}
function download(blob,name){const url=URL.createObjectURL(blob);urls.add(url);const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>{URL.revokeObjectURL(url);urls.delete(url);},1000);}
function downloadOriginal(){if(input&&!micBusy)download(input.blob,'noise-original-source.'+input.extension);}
const crcTable=Uint32Array.from({length:256},(_,value)=>{for(let bit=0;bit<8;bit++)value=value&1?0xedb88320^(value>>>1):value>>>1;return value>>>0;});
function crc32(bytes){let value=0xffffffff;for(const byte of bytes)value=crcTable[(value^byte)&255]^(value>>>8);return (value^0xffffffff)>>>0;}
function storedZIP(files){
  const locals=[],central=[];let offset=0,centralSize=0;
  for(const file of files){const name=encoder.encode(file.path),bytes=file.bytes,crc=crc32(bytes),header=new Uint8Array(30+name.length),view=new DataView(header.buffer);view.setUint32(0,0x04034b50,true);view.setUint16(4,20,true);view.setUint16(6,0x0800,true);view.setUint16(12,33,true);view.setUint32(14,crc,true);view.setUint32(18,bytes.length,true);view.setUint32(22,bytes.length,true);view.setUint16(26,name.length,true);header.set(name,30);locals.push(header,bytes);
    const directory=new Uint8Array(46+name.length),entry=new DataView(directory.buffer);entry.setUint32(0,0x02014b50,true);entry.setUint16(4,20,true);entry.setUint16(6,20,true);entry.setUint16(8,0x0800,true);entry.setUint16(14,33,true);entry.setUint32(16,crc,true);entry.setUint32(20,bytes.length,true);entry.setUint32(24,bytes.length,true);entry.setUint16(28,name.length,true);entry.setUint32(42,offset,true);directory.set(name,46);central.push(directory);centralSize+=directory.length;offset+=header.length+bytes.length;if(offset+centralSize+22>LIMITS.archiveBytes)throw failure('ZIP_LIMIT','ZIP의 96MiB 한도를 넘었습니다. 개별 파일로 보관하세요.');}
  const end=new Uint8Array(22),view=new DataView(end.buffer);view.setUint32(0,0x06054b50,true);view.setUint16(8,files.length,true);view.setUint16(10,files.length,true);view.setUint32(12,centralSize,true);view.setUint32(16,offset,true);return new Blob([...locals,...central,end],{type:'application/zip'});
}
async function exportComparison(format){
  if(!result||exporting||job||micBusy||closed)return false;const owner=result,generation=lifecycleGeneration;exporting=true;controls();error();
  try{const data=metadata(),json=JSON.stringify(data,null,2);if(format==='json'){download(new Blob([json],{type:'application/json'}),'noise-comparison.json');status('비교 metadata JSON 내려받기를 요청했습니다. 음성은 ZIP 또는 개별 파일로 보관하세요.');return true;}
    const files=[{path:'original-source.'+owner.input.extension,bytes:owner.input.bytes},{path:'raw-48k-mono.wav',bytes:owner.rawBytes},{path:'rnnoise-48k-mono.wav',bytes:owner.processedBytes},{path:'comparison.json',bytes:encoder.encode(json)},{path:'README.txt',bytes:encoder.encode('Local noise A/B comparison\n\noriginal-source keeps the original selected bytes unchanged. Both WAV paths use the same decoded, mono, resampled 48kHz PCM input. B uses the RNNoise algorithm recorded in comparison.json. No STT/API is invoked or automatically given these files. Metrics describe signals, not recognition or clinical accuracy. Recordings may contain private information; review before sharing. Metadata has no source filename, original practice identifier, actor, patient, session, or server URL. No ZIP import is provided. ZIP format: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT\n')}];
    const manifest={schema:1,purpose:'local_noise_ab_comparison',files:await Promise.all(files.map(async file=>({path:file.path,byteLength:file.bytes.length,sha256:await sha256(file.bytes)})))};
    if(closed||generation!==lifecycleGeneration||result!==owner)return false;const zip=storedZIP([{path:'manifest.json',bytes:encoder.encode(JSON.stringify(manifest,null,2))},...files]);download(zip,'noise-comparison.zip');status('원본·A/B WAV·metadata ZIP 내려받기를 요청했습니다. 실제 파일을 열어 확인하세요.');return true;
  }catch(err){if(!closed&&generation===lifecycleGeneration&&result===owner)error(err.message||'비교 파일을 내보내지 못했습니다.');return false;}
  finally{if(generation===lifecycleGeneration){exporting=false;controls();}}
}
$('file').onchange=()=>{const file=$('file').files?.[0];if(file)void selectInput(file);};
$('practice-refresh').onclick=refreshPractice;$('practice-select').onchange=controls;$('practice-load').onclick=loadPractice;
$('record').onclick=startMicrophone;$('record-stop').onclick=()=>stopMicrophone();$('record-cancel').onclick=cancelMicrophone;
$('process').onclick=processInput;$('cancel').onclick=()=>cancelProcessing();$('original-download').onclick=downloadOriginal;
$('raw-download').onclick=()=>{if(result&&!job)download(result.rawWav,'noise-raw-48k-mono.wav');};$('processed-download').onclick=()=>{if(result&&!job)download(result.processedWav,'noise-rnnoise-48k-mono.wav');};
$('compare-text').onclick=compareText;$('json').onclick=()=>exportComparison('json');$('zip').onclick=()=>exportComparison('zip');$('clear').onclick=()=>{clearInput();status('현재 화면의 입력·결과만 지웠습니다. 원본 파일과 연습 보관함은 유지합니다.');};
$('reference').oninput=()=>{$('reference-status').textContent='직접 편집한 공통 참조문입니다. 실제 녹음과 대조하세요.';};
window.addEventListener('pagehide',()=>{
  closed=true;lifecycleGeneration++;selection++;libraryGeneration++;cancelProcessing();
  micOwner++;micBusy=false;micStopping=false;micStarted=false;micAttemptId=null;audioCapture.cancel();
  selecting=false;libraryLoading=false;exporting=false;pausePlayers();
  for(const id of ['original-player','raw-player','processed-player'])revokePlayer(id);
  for(const url of urls)URL.revokeObjectURL(url);urls.clear();playerURLs.clear();controls();
});
window.addEventListener('pageshow',()=>{
  if(!closed)return;closed=false;
  // BFCache retains blobs and text, but pagehide revoked their URLs and cancelled
  // work. Restore only the display; never restart recording, processing or export.
  if(input){setPlayer('original-player',input.blob);$('original-player').hidden=false;}
  if(result){setPlayer('raw-player',result.rawWav);setPlayer('processed-player',result.processedWav);}
  status('화면을 복원했습니다. 진행 중이던 작업은 취소했고, 남아 있는 입력·결과는 직접 다시 실행하거나 내려받을 수 있습니다.');controls();
});
controls();
