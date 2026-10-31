import {QuickSession} from './quick-session.mjs';
import {isUnavailableAnswer} from './answer-match.mjs';

// Uses the same versioned question/answer contract as the quick voice page.
// Local deferral never fills a clinical value or marks a server question complete.
export class MainFollowup {
  constructor(client){this.client=client;this.flow=new QuickSession(client);this.deferred=new Map();this.last=null;this.changes=[];}
  observe(session=this.client.session){
    if(!session)return;
    if(this.last?.id!==session.id){this.deferred.clear();this.changes=[];}
    else if(session.version>this.last.version){
      this.changes=(session.latest_changes||[]).filter(c=>typeof c?.field==='string');
    }
    this.last={id:session.id,version:session.version};
    const offered=new Set((session.followup?.items||[]).filter(q=>q.askable!==false).map(q=>q.id));
    for(const key of this.deferred.keys())if(!offered.has(key))this.deferred.delete(key);
  }
  current(){
    const plan=this.client.session?.followup;if(!plan)return null;
    const next=(plan.items||[]).find(q=>q.askable!==false&&!this.deferred.has(q.id))||plan.next_question;
    return next&&!this.deferred.has(next.id)&&next.askable!==false?next:null;
  }
  defer(){const q=this.current();if(q)this.deferred.set(q.id,'담당자가 검토 때 확인');return q;}
  retryDeferred(){this.deferred.clear();}
  async answer(question,text,{unavailable=false}={}){
    if(!question?.id||this.current()?.id!==question.id)throw Error('확인 질문이 변경됐습니다. 최신 질문을 다시 확인하세요.');
    const result=await this.flow.answer(question,text,{unavailable:unavailable||isUnavailableAnswer(text)});
    if(result.answer?.accepted||result.answer?.unavailable)this.deferred.delete(question.id);
    else this.deferred.set(question.id,text);
    this.observe();return result;
  }
}

export function patientNavigation(server,patientId,actorId,page='/patient/'){
  const origin=new URL(server);
  if(!['http:','https:'].includes(origin.protocol)||origin.username||origin.password||origin.pathname!=='/'||origin.search||origin.hash)throw Error('서버 원점을 확인하세요.');
  if(typeof patientId!=='string'||patientId.length>128||typeof actorId!=='string'||actorId.length>80||!/^[A-Za-z0-9][A-Za-z0-9._:@-]*$/.test(patientId)||!/^[A-Za-z0-9][A-Za-z0-9._:@-]*$/.test(actorId))throw Error('환자·작업자 ID를 확인하세요.');
  if(!['/patient/','/patient/transport-change.html'].includes(page))throw Error('진행 화면 경로를 확인하세요.');
  const target=new URL(page,origin.origin);target.searchParams.set('pid',patientId);target.searchParams.set('actor_id',actorId);
  return target.href;
}
