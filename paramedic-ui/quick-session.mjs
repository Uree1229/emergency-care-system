/** A long utterance is one API operation; partial STT fragments never edit a draft. */
import {requestId} from './voice-client.mjs';

export const FOLLOWUP_PROFILE = 'voice_extended_v1';
const MODES = ['auto', 'dictation', 'command'];

function checkText(text) {
  if (typeof text !== 'string' || !text.trim() || text.length > 4000) throw new Error('최종 인식문은 1~4000자여야 합니다.');
}

export class QuickSession {
  constructor(client, makeId = requestId) {
    this.client = client;
    this.makeId = makeId;
    this.job = null;
  }
  async apply(text, mode = 'dictation') {
    if (this.job) throw new Error('진행 중인 요청을 먼저 확인하세요.');
    if (!MODES.includes(mode)) throw new Error('지원하지 않는 말하기 모드입니다.');
    checkText(text);
    if (mode === 'command' && !this.client.session) throw new Error('환자 상태를 먼저 말해 주세요.');
    this.job = {text, mode, utteranceId: this.makeId(), phase: this.client.session ? 'write' : 'create'};
    try {
      if (this.job.phase === 'create') await this.client.create();
      return await this._write();
    } catch (error) {
      if (!this.client.pending) this.job = null;
      throw error;
    }
  }
  async _write() {
    this.job.phase = 'write';
    const {text, mode, utteranceId} = this.job;
    // Even submit commands only reach the transcript endpoint, never /submit.
    const result = await this.client.write('transcripts', {
      text, mode, utterance_id: utteranceId, is_final: true,
    });
    this.job = null;
    return result;
  }
  /** Answer only the question the server offered; the server re-checks it against the current version. */
  async answer(question, text, {unavailable = false} = {}) {
    if (this.job) throw new Error('진행 중인 요청을 먼저 확인하세요.');
    if (!this.client.session) throw new Error('환자 상태를 먼저 말해 주세요.');
    if (!question?.id) throw new Error('답할 확인 질문이 없습니다.');
    checkText(text);
    this.job = {text, questionId: question.id, utteranceId: this.makeId(), phase: 'answer'};
    try {
      const result = await this.client.write('followup/answers', {
        question_id: question.id, profile: FOLLOWUP_PROFILE, utterance_id: this.job.utteranceId, text,
        ...(unavailable ? {unavailable: true} : {}),
      });
      this.job = null;
      return result;
    } catch (error) {
      if (!this.client.pending) this.job = null;
      throw error;
    }
  }
  async retry() {
    if (!this.job || !this.client.pending) throw new Error('재시도할 요청이 없습니다.');
    const phase = this.job.phase;
    try {
      const result = await this.client.retry();
      if (phase === 'create') return await this._write();
      this.job = null;
      return result;
    } catch (error) {
      if (!this.client.pending) this.job = null;
      throw error;
    }
  }
}
