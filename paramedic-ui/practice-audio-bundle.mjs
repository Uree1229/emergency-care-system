/** Local-only stored ZIP export. No compression library, fetch, or audio synthesis.
 * ZIP records follow PKWARE APPNOTE 6.3.10, sections 4.3.7/4.3.12/4.3.16.
 * https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
 */
import * as practiceData from './command-practice-data.mjs';

export const PRACTICE_BUNDLE_LIMITS=Object.freeze({audioFileBytes:20*1024*1024,archiveBytes:256*1024*1024,attempts:500});
const encoder=new TextEncoder();
const extensions=new Map([['audio/webm','webm'],['audio/ogg','ogg'],['audio/mp4','mp4']]);
const error=(code,message)=>Object.assign(new Error(message),{code});
const invalid=message=>{throw error('INVALID_PRACTICE_AUDIO',message||'음성 자료와 시도 기록이 일치하지 않습니다.');};
const limited=()=>{throw error('PRACTICE_BUNDLE_LIMIT','음성 묶음의 용량 또는 파일 수 상한을 넘었습니다. 기존 자료는 유지됩니다.');};
const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const safeCode=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,80}$/u.test(value);
const hex=bytes=>Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');
const mutableSnapshotKeys=new Set(['expectedAction','referenceConfirmed','notes','audio','actualAudioSaved','imported']);
const stable=value=>JSON.stringify(value&&typeof value==='object'?Array.isArray(value)?value.map(item=>JSON.parse(stable(item))):Object.fromEntries(Object.keys(value).sort().map(key=>[key,JSON.parse(stable(value[key]))])):value);
function captureAssociation(value){
  if(!plain(value))invalid('음성과 연결된 원래 시도 기록을 확인할 수 없습니다.');
  return Object.fromEntries(Object.entries(value).filter(([key])=>!mutableSnapshotKeys.has(key)));
}
async function sha256(bytes){
  if(!globalThis.crypto?.subtle?.digest)throw error('PRACTICE_HASH_UNAVAILABLE','이 브라우저에서는 SHA-256 무결성 검사를 사용할 수 없습니다. HTTPS 또는 localhost에서 다시 시도하세요.');
  return hex(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',bytes)));
}
const crcTable=Uint32Array.from({length:256},(_,value)=>{for(let bit=0;bit<8;bit++)value=value&1?0xedb88320^(value>>>1):value>>>1;return value>>>0;});
function crc32(bytes){let value=0xffffffff;for(const byte of bytes)value=crcTable[(value^byte)&255]^(value>>>8);return (value^0xffffffff)>>>0;}
function dosTimestamp(now){
  const date=new Date(Math.min(Math.max(now,Date.UTC(1980,0,1)),Date.UTC(2107,11,31,23,59,58)));
  return {time:date.getUTCHours()<<11|date.getUTCMinutes()<<5|Math.floor(date.getUTCSeconds()/2),date:(date.getUTCFullYear()-1980)<<9|(date.getUTCMonth()+1)<<5|date.getUTCDate()};
}
function storedZIP(files,now){
  const locals=[],central=[],stamp=dosTimestamp(now);let offset=0,centralSize=0;
  if(files.length>=65535)limited();
  for(const file of files){
    const name=encoder.encode(file.path),bytes=file.bytes,crc=crc32(bytes);
    if(name.length>65535||bytes.length>=0xffffffff||offset>=0xffffffff)limited();
    const header=new Uint8Array(30+name.length),view=new DataView(header.buffer);
    view.setUint32(0,0x04034b50,true);view.setUint16(4,20,true);view.setUint16(6,0x0800,true);
    view.setUint16(10,stamp.time,true);view.setUint16(12,stamp.date,true);view.setUint32(14,crc,true);
    view.setUint32(18,bytes.length,true);view.setUint32(22,bytes.length,true);view.setUint16(26,name.length,true);header.set(name,30);
    locals.push(header,bytes);
    const directory=new Uint8Array(46+name.length),entry=new DataView(directory.buffer);
    entry.setUint32(0,0x02014b50,true);entry.setUint16(4,20,true);entry.setUint16(6,20,true);entry.setUint16(8,0x0800,true);
    entry.setUint16(12,stamp.time,true);entry.setUint16(14,stamp.date,true);entry.setUint32(16,crc,true);
    entry.setUint32(20,bytes.length,true);entry.setUint32(24,bytes.length,true);entry.setUint16(28,name.length,true);
    entry.setUint32(42,offset,true);directory.set(name,46);central.push(directory);centralSize+=directory.length;offset+=header.length+bytes.length;
    if(offset+centralSize+22>PRACTICE_BUNDLE_LIMITS.archiveBytes)limited();
  }
  const end=new Uint8Array(22),endView=new DataView(end.buffer);
  endView.setUint32(0,0x06054b50,true);endView.setUint16(8,files.length,true);endView.setUint16(10,files.length,true);
  endView.setUint32(12,centralSize,true);endView.setUint32(16,offset,true);
  return new Blob([...locals,...central,end],{type:'application/zip'});
}

/**
 * audioAssets is an array keyed by attemptId. An available entry owns a Blob;
 * missing/error/not_recorded entries explicitly describe absence. No missing
 * bytes are fabricated. The original dataset is never mutated or relabelled.
 */
export async function buildPracticeAudioBundle(dataset,audioAssets=[],{now=Date.now()}={}){
  if(!Number.isSafeInteger(now)||now<0)invalid('음성 묶음의 생성 시각이 올바르지 않습니다.');
  // Existing core validation and CSV escaping remain the single data contract.
  const originalJSON=practiceData.exportPracticeJSON(dataset);let canonical=JSON.parse(originalJSON);
  if(!Array.isArray(audioAssets)||audioAssets.length>PRACTICE_BUNDLE_LIMITS.attempts)invalid();
  const attempts=new Map(canonical.attempts.map(attempt=>[attempt.id,attempt])),assets=new Map();
  for(const asset of audioAssets){
    if(!plain(asset)||typeof asset.attemptId!=='string'||!attempts.has(asset.attemptId)||assets.has(asset.attemptId))invalid();
    assets.set(asset.attemptId,asset);
  }
  const files=[];
  const audio=[];let audioBytes=0;
  for(const attempt of canonical.attempts){
    const asset=assets.get(attempt.id),expected=attempt.audio;
    const expectedBytes=expected?.byteLength||0,expectedHash=expected?.sha256||null;
    const row={attemptId:attempt.id,availability:asset?.availability||(expectedBytes||expectedHash?'missing':expected&&['failed','unavailable','empty','interrupted'].includes(expected.status)?'error':'not_recorded'),included:false,path:null,mime:null,size:0,sha256:null,errorCode:null};
    if(!['available','missing','error','not_recorded'].includes(row.availability))invalid();
    if(row.availability==='not_recorded'&&(expectedBytes||expectedHash))row.availability='missing';
    if(row.availability==='available'){
      if(attempt.source!=='human_voice'||!(asset.blob instanceof Blob))invalid();
      if(stable(captureAssociation(asset.snapshot))!==stable(captureAssociation(attempt)))invalid('같은 시도 ID에 연결된 음성의 원문·시험 조건이 현재 기록과 일치하지 않습니다.');
      const captureError=expected?.errorCode||'';if(captureError&&!safeCode(captureError))invalid('부분 녹음의 오류 코드를 확인할 수 없습니다.');
      const blob=asset.blob,mime=blob.type.toLowerCase(),extension=extensions.get(mime.split(';',1)[0]);
      if(!extension||!Number.isSafeInteger(blob.size)||blob.size<=0)invalid();
      if(blob.size>PRACTICE_BUNDLE_LIMITS.audioFileBytes||audioBytes+blob.size>PRACTICE_BUNDLE_LIMITS.archiveBytes)limited();
      if(asset.mime!==undefined&&asset.mime!==mime||asset.contentType!==undefined&&asset.contentType!==mime||asset.extension!==undefined&&asset.extension!==extension||asset.size!==undefined&&asset.size!==blob.size||asset.byteLength!==undefined&&asset.byteLength!==blob.size)invalid();
      if(asset.durable!==undefined&&typeof asset.durable!=='boolean')invalid();
      if(asset.sha256!==undefined&&(typeof asset.sha256!=='string'||!/^[a-f0-9]{64}$/u.test(asset.sha256)))invalid();
      const bytes=new Uint8Array(await blob.arrayBuffer());
      if(bytes.byteLength!==blob.size)invalid();
      const digest=await sha256(bytes);if(asset.sha256!==undefined&&asset.sha256!==digest)invalid('음성 파일의 SHA-256이 보관된 값과 일치하지 않습니다.');
      if(expectedHash&&expectedHash!==digest||expectedBytes&&expectedBytes!==bytes.length||expected?.contentType&&expected.contentType!==mime||expected?.extension&&expected.extension!==extension)invalid('현재 시도의 음성 메타데이터와 실제 파일이 일치하지 않습니다.');
      const path='audio/'+String(audio.length+1).padStart(4,'0')+'-'+encodeURIComponent(attempt.id)+'.'+extension;
      Object.assign(row,{included:true,path,mime,size:bytes.length,sha256:digest,errorCode:captureError||null});files.push({path,mime,bytes});audioBytes+=bytes.length;
      if(audioBytes>PRACTICE_BUNDLE_LIMITS.archiveBytes)limited();
      canonical=practiceData.attachPracticeAudio(canonical,attempt.id,{status:asset.durable===true?'stored':'memory_only',contentType:mime,extension,byteLength:bytes.length,sha256:digest,
        startedAt:asset.startedAt??expected?.startedAt??attempt.timing.startedAt,endedAt:asset.endedAt??expected?.endedAt??attempt.timing.endedAt,errorCode:captureError});
    }else{
      if(asset?.blob!==undefined&&asset.blob!==null)invalid();
      if(row.availability==='error'){const code=asset?.errorCode||expected?.errorCode||'AUDIO_'+(expected?.status||'ERROR').toUpperCase();if(!safeCode(code))invalid();row.errorCode=code;}
      if(expected&&(row.availability==='missing'||row.availability==='error'))canonical=practiceData.attachPracticeAudio(canonical,attempt.id,{...expected,status:'missing',errorCode:row.errorCode||'AUDIO_BYTES_MISSING'});
    }
    audio.push(row);
  }
  files.unshift({path:'dataset-original.json',mime:'application/json',bytes:encoder.encode(originalJSON)},
    {path:'dataset.json',mime:'application/json',bytes:encoder.encode(practiceData.exportPracticeJSON(canonical))},
    {path:'attempts.csv',mime:'text/csv;charset=utf-8',bytes:encoder.encode(practiceData.exportPracticeCSV(canonical))});
  const readme='Command practice export\n\nmanifest.json lists exactly which audio files this ZIP contains, with SHA-256, MIME and byte size.\ndataset-original.json preserves the input transcript dataset exactly under its own schema.\ndataset.json and attempts.csv contain a copy enriched from currently verified audio bytes. The stored/memory_only and actualAudioSaved flags describe browser persistence, not capture quality or completeness; manifest availability describes actual ZIP file inclusion. Partial recordings are included without changing their bytes and retain a nonempty errorCode warning in the manifest and dataset. Missing current bytes clear stored claims in the enriched copy.\nMissing/error/not_recorded rows contain no fabricated audio. No audio is sent to a server.\nMicrophone route labels do not prove recognition accuracy, speech content, or speaker identity. Audio may contain private information; review files before sharing.\nZIP import is not supported; dataset JSON import restores metadata, not audio bytes.\n';
  files.push({path:'README.txt',mime:'text/plain;charset=utf-8',bytes:encoder.encode(readme)});
  const index=[];for(const file of files)index.push({path:file.path,mime:file.mime,size:file.bytes.length,sha256:await sha256(file.bytes)});
  const manifest={schema:1,purpose:'command_practice_audio_bundle',createdAt:now,actualAudioIncluded:audio.some(row=>row.availability==='available'),
    datasetAudioFlagMeans:'verified_browser_persistence_not_zip_membership',audio,files:index};
  const manifestFile={path:'manifest.json',bytes:encoder.encode(JSON.stringify(manifest,null,2))};
  const blob=storedZIP([manifestFile,...files],now);
  return {blob,manifest,filename:'command-practice-'+now+'.zip'};
}
