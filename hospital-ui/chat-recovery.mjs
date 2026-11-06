// 병원 이송 대화에서 결과를 확인하지 못한 원요청만 대화 범위(서버·병원·환자)마다 한 건씩 보관한다.
// 채팅 사본은 저장하지 않으며, 재전송은 항상 사람이 원래 범위로 돌아와 명시적으로 실행한다.
const PREFIX='capstone.hospital.chat.v1:';
export const PENDING_TTL_MS=24*60*60*1000;

const uuid=()=>globalThis.crypto?.randomUUID?.()??`hospital-${Date.now()}-${Math.random().toString(16).slice(2)}`;
const validRequest=r=>!!r&&typeof r.body==='string'&&r.body.length>0&&typeof r.client_message_id==='string'&&r.client_message_id.length>0
  &&(r.hospital_session_id===null||Number.isInteger(r.hospital_session_id))&&typeof r.sender_name==='string';

export class ChatSaveError extends Error {
  constructor(){super('전송 요청을 브라우저에 저장하지 못해 메시지를 보내지 않았습니다. 브라우저 저장소를 확인한 뒤 다시 시도하세요.');this.name='ChatSaveError';}
}
export function newChatRequest({body,session,senderName,makeId=uuid}){
  return {body,client_message_id:makeId(),hospital_session_id:session,sender_name:senderName};
}
// 서버가 판단한 거절(4xx)만 결과가 확정된 실패다. 네트워크 오류와 5xx는 저장 여부를 알 수 없다.
export const isRejected=error=>!!error?.status&&error.status<500;

export class ChatRecoveryStore {
  constructor({storage,now=()=>Date.now()}){this.storage=storage;this.now=now;}
  key({server,hospital,patient}){return `${PREFIX}${encodeURIComponent(JSON.stringify([server,hospital,patient]))}`;}
  remove(key){try{this.storage?.removeItem(key);return true;}catch{return false;}}
  read(key){
    let record=null;
    try{record=JSON.parse(this.storage.getItem(key));}catch{this.remove(key);return null;}
    if(!record)return null;
    const usable=validRequest(record.request)&&Number.isFinite(record.savedAt)&&this.key(record)===key&&this.now()-record.savedAt<PENDING_TTL_MS;
    if(!usable){this.remove(key);return null;}
    return record;
  }
  load(scope){return this.storage?this.read(this.key(scope)):null;}
  list(server,hospital){
    if(!this.storage)return [];
    const keys=[];
    try{for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(PREFIX))keys.push(key);}}catch{return [];}
    return keys.map(key=>this.read(key)).filter(record=>record&&record.server===server&&record.hospital===hospital);
  }
  // 다시 읽어 확인까지 끝나야 POST를 시작할 수 있다.
  save(scope,request){
    const key=this.key(scope),raw=JSON.stringify({...scope,savedAt:this.now(),request});
    try{this.storage.setItem(key,raw);if(this.storage.getItem(key)!==raw)throw new Error('read-back mismatch');}
    catch{this.remove(key);throw new ChatSaveError();}
    return request;
  }
  clear(scope,clientMessageId){
    if(this.load(scope)?.request.client_message_id!==clientMessageId)return false;
    return this.remove(this.key(scope));
  }
}
