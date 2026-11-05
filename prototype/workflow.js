(function (root) {
  'use strict';
  const limits = {warning: 60000, retry: 120000};
  function create(count, now, mode) {
    if (![1, 5, 10].includes(count)) throw Error('Invalid count');
    return {mode, started: now, ended: null, hospital: {state: 'available', expires: null}, events: [],
      requests: Array.from({length: count}, (_, i) => ({id: `SIM-${i + 1}`, created: now,
        state: 'pending', attempts: 1, lastAttempt: now, warned: false, clicks: 0, answered: null,
        target: i % 3 === 1 ? 'decline' : 'accept', targetReason: i % 3 === 1 ? '인력' : '', reason: ''}))};
  }
  function tick(s, now) {
    if (s.ended !== null) return;
    if (s.hospital.state === 'restricted' && now >= s.hospital.expires) {
      s.hospital.state = 'recheck';
      s.events.push({at: now, type: 'hospital_recheck'});
    }
    for (const r of s.requests) if (r.state === 'pending' && !r.warned && now - r.lastAttempt >= limits.warning) {
      r.warned = true;
      s.events.push({at: now, id: r.id, type: 'warning'});
    }
  }
  function act(s, id, action, now, reason = '') {
    tick(s, now);
    const r = s.requests.find(r => r.id === id);
    if (!r || r.state !== 'pending' || s.ended !== null) return false;
    if (action === 'accept' && s.hospital.state !== 'available') return false;
    if (action === 'decline' && !['인력', '장비', '병상', '기타'].includes(reason)) return false;
    if (action === 'retry' && now - r.lastAttempt < limits.retry) return false;
    if (!['accept', 'decline', 'retry'].includes(action)) return false;
    r.clicks++;
    if (action === 'retry') { r.attempts++; r.lastAttempt = now; r.warned = false; }
    else { r.state = action; r.answered = now; r.reason = reason; }
    s.events.push({at: now, id, type: action, reason, attempt: r.attempts});
    return true;
  }
  function extraClick(s, id, now) {
    const r = s.requests.find(r => r.id === id);
    if (s.ended === null && r && r.state === 'pending') {
      r.clicks++;
      s.events.push({at: now, id, type: 'ui_click'});
    }
  }
  function hospital(s, action, now) {
    tick(s, now);
    if (s.ended !== null) return false;
    if (action === 'restrict') s.hospital = {state: 'restricted', expires: now + 60000};
    else if (action === 'confirm') s.hospital = {state: 'available', expires: null};
    else return false;
    s.events.push({at: now, type: 'hospital_' + action});
    return true;
  }
  function summary(s) {
    const done = s.requests.filter(r => r.answered !== null);
    const sorted = done.map(r => (r.answered - r.created) / 1000).sort((a,b) => a-b);
    const n = sorted.length;
    return {completed: n, total: s.requests.length,
      medianSeconds: n ? (sorted[Math.floor((n-1)/2)] + sorted[Math.floor(n/2)]) / 2 : null,
      p95Seconds: n ? sorted[Math.ceil(n*0.95)-1] : null,
      clicks: s.requests.reduce((v,r) => v+r.clicks, 0),
      unansweredRate: 1-n/s.requests.length,
      errors: done.filter(r => r.state !== r.target || (r.target === 'decline' && r.reason !== r.targetReason)).length};
  }
  const api = {create, tick, act, extraClick, hospital, summary, limits};
  if (typeof module !== 'undefined') module.exports = api;
  root.Workflow = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
