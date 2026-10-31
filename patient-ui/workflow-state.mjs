// Common-server workflow state. Receipt writes require an actual rendered row.
export function validActor(value) {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,79}$/u.test(value) && !/[\r\n]/u.test(value);
}
export const ACTOR_REQUIREMENT = '작업자 ID는 영문·숫자로 시작하고 영문·숫자·점(.)·밑줄(_)·콜론(:)·@·하이픈(-)만 사용해 1~80자로 입력하세요. 한글과 공백은 사용할 수 없습니다.';
export function resolveActor(requested, saved, generate) {
  // Explicit or previously saved invalid values require correction, not a new identity.
  if (requested !== null && requested !== undefined) return requested;
  if (saved !== null && saved !== undefined) return saved;
  return generate();
}
export function workflowLink(server, pid, actor) {
  if (!pid || !validActor(actor)) return null;
  const target = new URL(server.replace(/\/$/,'') + '/paramedic/');
  target.search = new URLSearchParams({pid, actor_id: actor, server}).toString();
  return target.href;
}
export function recommendationView(data, patient, now = Date.now() / 1000) {
  const terminal = ['ARRIVED', 'COMPLETED', 'CANCELLED'].includes(patient?.state);
  let status = terminal ? 'NOT_APPLICABLE' : data?.status || 'UNAVAILABLE';
  if (status === 'READY' && (!Number.isFinite(data?.expires_at) || data.expires_at <= now)) status = 'STALE';
  return {status, result: status === 'READY' ? data?.result || null : null,
    availability: data?.availability || {worker_state: 'UNKNOWN', accepting_requests: null},
    created_at: data?.created_at ?? null, expires_at: data?.expires_at ?? null, revision: data?.revision ?? null};
}
const receipt = value => typeof value === 'number' && Number.isFinite(value);
function mergeEvent(old, next) {
  return {...old, ...next, delivered_at: old?.delivered_at ?? next.delivered_at ?? null,
    acknowledged_at: old?.acknowledged_at ?? next.acknowledged_at ?? null};
}
export class PatientWorkflow {
  constructor({request, onChange = () => {}, isVisible = () => true, now = () => Date.now() / 1000}) {
    this.request = request; this.onChange = onChange; this.isVisible = isVisible; this.now = now;
    this.serial = 0; this.context = null; this.reset();
  }
  reset() {
    this.patient = null; this.recommendation = null; this.inputReview = null;
    this.recommendationError = ''; this.reviewError = ''; this.eventError = '';
    this.receiptErrors = new Map();
    this.events = new Map(); this.cursor = 0; this.delivering = new Set(); this.acknowledging = new Set(); this.failedDeliveries = new Set();
    this.eventFlight = null; this.recoverPending = false; this.planSequence = 0; this.replanning = false;
    this.recoveryHasMore = false;
  }
  setContext(context) {
    this.serial++; this.context = context?.pid && validActor(context.actor) ? Object.freeze({...context, serial: this.serial}) : null;
    this.reset(); this.emit();
  }
  current(token) { return !!token && this.context === token; }
  emit() { this.onChange(this.snapshot()); }
  snapshot() {
    return {context: this.context, patient: this.patient, plan: recommendationView(this.recommendation, this.patient, this.now()),
      inputReview: this.inputReview, recommendationError: this.recommendationError, reviewError: this.reviewError,
      eventError: [...this.receiptErrors.values(),this.eventError].filter(Boolean).join('\n'), events: [...this.events.values()].sort((a,b) => b.event_id - a.event_id),
      acknowledging: new Set(this.acknowledging), replanning: this.replanning, recoveryHasMore: this.recoveryHasMore};
  }
  setPatient(patient) {
    if (!this.context || patient?.id !== this.context.pid) return;
    if (this.patient && (patient.version !== this.patient.version || patient.state !== this.patient.state)) {
      this.planSequence++; this.recommendation = null; this.inputReview = null;
    }
    this.patient = patient; this.emit();
  }
  invalidatePlan() { this.planSequence++; this.recommendation = null; this.emit(); }
  async refreshPlan() {
    const token = this.context; if (!token) return;
    if (['ARRIVED','COMPLETED','CANCELLED'].includes(this.patient?.state)) { this.recommendation = {status:'NOT_APPLICABLE'}; this.emit(); return; }
    const sequence = ++this.planSequence;
    const version = this.patient?.version;
    const results = await Promise.allSettled([
      this.request(token, '/recommendation'), this.request(token, '/input-review'),
    ]);
    if (!this.current(token) || sequence !== this.planSequence || version !== this.patient?.version) return;
    const [plan, review] = results;
    this.recommendation = plan.status === 'fulfilled' ? plan.value : null;
    this.recommendationError = plan.status === 'rejected' ? plan.reason.message : '';
    if (review.status === 'fulfilled' && review.value.patient_id === token.pid &&
        (version === undefined || review.value.patient_version === version)) {
      this.inputReview = review.value.input_review; this.reviewError = '';
    } else {
      this.inputReview = plan.status === 'fulfilled' ? plan.value.input_review || null : null;
      this.reviewError = review.status === 'rejected' ? review.reason.message : '환자 정보가 바뀌어 확인 항목을 다시 조회해야 합니다.';
    }
    this.emit();
  }
  async replan() {
    const token = this.context;
    if (!token || !this.patient || this.replanning || recommendationView(this.recommendation,this.patient,this.now()).availability.accepting_requests === false ||
        ['ARRIVED','COMPLETED','CANCELLED'].includes(this.patient?.state)) return false;
    this.replanning = true; this.invalidatePlan();
    try {
      await this.request(token, '/replan', 'POST', {});
      if (!this.current(token)) return false;
      this.recommendationError = ''; return true;
    } catch (error) {
      if (this.current(token)) this.recommendationError = `${error.message}${error.status === 409 ? ' · 최신 상태를 다시 확인하세요.' : ''}`;
      return false;
    } finally {
      if (this.current(token)) { this.replanning = false; const error = this.recommendationError; await this.refreshPlan();
        if (this.current(token) && error) { this.recommendationError = error; this.emit(); } }
    }
  }
  checkPage(data, token) {
    if (data.scope?.patient_id !== token.pid || data.scope?.actor_id !== token.actor || data.scope?.role !== 'paramedic') throw Error('중요 사건의 환자·작업자 범위가 다릅니다.');
    for (const item of data.items || []) {
      if (!Number.isSafeInteger(item.event_id) || item.event_id <= 0 || item.patient_id !== token.pid) throw Error('중요 사건 식별자가 올바르지 않습니다.');
    }
  }
  acceptEvent(item) {
    const merged=mergeEvent(this.events.get(item.event_id),item);this.events.set(item.event_id,merged);
    if(receipt(merged.delivered_at))this.receiptErrors.delete('delivered:'+item.event_id);
    if(receipt(merged.acknowledged_at))this.receiptErrors.delete('ack:'+item.event_id);
  }
  async refreshEvents({recover = false} = {}) {
    const token = this.context; if (!token) return;
    if (this.eventFlight?.token === token) { this.recoverPending ||= recover; return this.eventFlight.promise; }
    const flight = {token}; this.eventFlight = flight;
    this.failedDeliveries.clear();
    flight.promise = (async () => {
      try {
        let after = this.cursor, more = true;
        while (more && this.current(token)) {
          const data = await this.request(token, `/important-events?${new URLSearchParams({actor_id:token.actor, after_id:String(after), limit:'100'})}`);
          if (!this.current(token)) return;
          this.checkPage(data, token);
          for (const item of data.items || []) this.acceptEvent(item);
          const next = data.next_after_id;
          if (!Number.isSafeInteger(next) || next < after || (data.has_more && next <= after)) throw Error('중요 사건의 다음 페이지가 진행되지 않습니다.');
          this.cursor = after = next; more = !!data.has_more; this.emit();
        }
        if (recover && this.current(token)) {
          const data = await this.request(token, `/important-events?${new URLSearchParams({actor_id:token.actor, unacknowledged_only:'true', limit:'100'})}`);
          if (!this.current(token)) return;
          this.checkPage(data, token);
          for (const item of data.items || []) this.acceptEvent(item);
          // Unacknowledged mode has no forward cursor. Ack followed by recovery exposes the next page.
          this.recoveryHasMore = !!data.has_more;
        }
        if (this.current(token)) { this.eventError = ''; this.emit(); }
      } catch (error) { if (this.current(token)) { this.eventError = error.message; this.emit(); } }
      finally {
        if (this.eventFlight === flight) this.eventFlight = null;
        if (this.current(token) && this.recoverPending) { this.recoverPending = false; await this.refreshEvents({recover:true}); }
      }
    })();
    return flight.promise;
  }
  async markVisible(ids, token = this.context) {
    if (!this.current(token) || !this.isVisible()) return;
    for (const id of ids) {
      const item = this.events.get(id);
      if (!this.current(token) || !this.isVisible()) return;
      if (!item || receipt(item.delivered_at) || this.delivering.has(id) || this.failedDeliveries.has(id)) continue;
      this.delivering.add(id);
      try {
        const result = await this.request(token, `/important-events/${id}/delivered`, 'POST', {actor_id:token.actor});
        if (!this.current(token)) return;
        if (result.event?.event_id !== id || result.event?.patient_id !== token.pid || !receipt(result.event.delivered_at)) throw Error('사건 수신 응답의 범위가 다릅니다.');
        this.acceptEvent(result.event); this.eventError = '';
      } catch (error) { if (this.current(token)) { this.failedDeliveries.add(id); this.receiptErrors.set('delivered:'+id,`사건 #${id} 수신 기록 미완료: ${error.message}`); } }
      finally { if (this.current(token)) { this.delivering.delete(id); this.emit(); } }
    }
  }
  async acknowledge(id) {
    const token = this.context, item = this.events.get(id);
    if (!token || !this.isVisible() || !item || !receipt(item.delivered_at) || receipt(item.acknowledged_at) || this.acknowledging.has(id)) return false;
    this.acknowledging.add(id); this.receiptErrors.delete('ack:'+id); this.emit();
    try {
      const result = await this.request(token, `/important-events/${id}/acknowledge`, 'POST', {actor_id:token.actor});
      if (!this.current(token)) return false;
      if (result.event?.event_id !== id || result.event?.patient_id !== token.pid || !receipt(result.event.acknowledged_at)) throw Error('사건 확인 응답을 검증하지 못했습니다.');
      this.acceptEvent(result.event); this.eventError = ''; return true;
    } catch (error) { if (this.current(token)) this.receiptErrors.set('ack:'+id,`사건 #${id} 확인 기록 미완료: ${error.message}`); return false; }
    finally { if (this.current(token)) { this.acknowledging.delete(id); this.emit(); await this.refreshEvents({recover:true}); } }
  }
  async refresh({recover = false} = {}) { return Promise.allSettled([this.refreshPlan(), this.refreshEvents({recover})]); }
}
