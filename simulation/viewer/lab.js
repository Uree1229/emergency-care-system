'use strict';

// Three-view shell for lab.html: 지도 재생 / 결과 비교 / 발표.
// It reuses app.js state, replay and tables unchanged. The comparison and story
// text is assembled from recorded events and outcomes only; nothing here
// re-runs or second-guesses an allocation decision.
(() => {
  // Brighter policy colours read on the dark map and on the light pages.
  ['#3D8FD6', '#D98A2B', '#9C7CE3', '#2FA88A', '#E0607E'].forEach((color, i) => { COLORS[i] = color; });
  state.scenarios.forEach(s => s.branches.forEach((b, i) => { b.color = COLORS[i % COLORS.length]; }));

  const VIEWS = ['replay', 'compare', 'story'];
  const ASSIGN_TYPES = new Set(['commit', 'redirect', 'transfer_depart']);
  const REGION_LABELS = [{ name: '수원', lat: 37.26, lon: 127.0 }, { name: '용인', lat: 37.25, lon: 127.19 }, { name: '화성', lat: 37.15, lon: 126.90 }, { name: '오산', lat: 37.15, lon: 127.06 }, { name: '평택', lat: 36.99, lon: 127.06 }, { name: '안성', lat: 37.01, lon: 127.24 }, { name: '아산', lat: 36.82, lon: 126.95 }, { name: '천안', lat: 36.82, lon: 127.16 }];
  const dirty = { geo: true, compare: true, story: true };
  const story = { left: null, right: null, step: 0, steps: [], pair: [], involved: [] };
  const historyCache = new WeakMap();
  let view = 'replay', multiplesWidth = 0;

  const num = (v) => v == null || v === '' ? NaN : Number(v);
  const isVoice = () => typeof voiceCase === 'function' && voiceCase();
  const validLoc = (l) => Array.isArray(l) && l.length >= 2 && l.every(Number.isFinite) && !(l[0] === 0 && l[1] === 0);
  const hospitalLoc = (id) => { const h = hospitalById(id); return h ? locOf(h) : null; };
  const shortPatient = (p) => String(p?.id ?? p ?? '').replace(/^.*-/, '');
  const patientName = (p) => { const id = shortPatient(p); return /^P\d/i.test(id) ? id : `환자 ${id}`; };
  const shortHospital = (id) => id ? hospitalName(id).replace(/^의료법인[^병]*재단\s*/, '').trim() : '';
  const tinyHospital = (id) => { const name = shortHospital(id), tiny = name.replace(/병원$/, ''); return tiny.length >= 2 ? tiny : name; };
  const groupBy = (items, key) => { const map = new Map(); items.forEach(item => { const k = key(item); if (!map.has(k)) map.set(k, []); map.get(k).push(item); }); return map; };
  const finalEvents = (b) => b.events.filter(e => when(e) <= state.duration + 1e-7);
  const asks = (b, until = Infinity) => b.events.filter(e => typeOf(e) === 'ask' && when(e) <= until + 1e-7).length;
  const committedCount = (b) => new Set(finalEvents(b).filter(e => typeOf(e) === 'commit').map(patientId)).size;
  function atTime(t, fn) { const saved = state.time; state.time = t; try { return fn(); } finally { state.time = saved; } }
  function rich(...parts) { const frag = document.createDocumentFragment(); parts.flat().forEach(part => { if (part != null && part !== false) frag.append(typeof part === 'object' ? part : String(part)); }); return frag; }
  const bold = (text) => node('b', '', text);
  const idList = (ps, max = 8) => `${ps.slice(0, max).map(shortPatient).join('·')}${ps.length > max ? ` 외 ${ps.length - max}명` : ''}`;
  function branchTag(list) {
    const span = node('span', 'why-branch-group');
    list.forEach((b, i) => { if (i) span.append('·'); const tag = node('span', 'why-branch', b.displayId); tag.style.setProperty('--branch-color', b.color); tag.title = b.label; span.append(tag); });
    return span;
  }

  // Assignment history per patient: commit / redirect / transfer destinations, deduplicated.
  function histories(b) {
    let map = historyCache.get(b);
    if (map) return map;
    map = new Map();
    for (const e of b.events) {
      if (!ASSIGN_TYPES.has(typeOf(e))) continue;
      const pid = patientId(e), hid = hospitalId(e);
      if (!pid || !hid) continue;
      const list = map.get(pid) ?? [];
      if (list.at(-1)?.hospital !== hid) list.push({ t: when(e), hospital: hid, type: typeOf(e) });
      map.set(pid, list);
    }
    historyCache.set(b, map);
    return map;
  }
  const assignmentHistory = (b, p) => histories(b).get(String(p.id)) ?? [];
  const outcomeOf = (b, p) => b.patient_outcomes.find(o => String(o.id) === String(p.id)) ?? null;
  function finalHospital(b, p) {
    const outcome = outcomeOf(b, p);
    if (outcome && 'destination_hospital_id' in outcome) return outcome.destination_hospital_id ?? null;
    return assignmentHistory(b, p).at(-1)?.hospital ?? null;
  }
  function arrivalOf(b, p) { const t = num(outcomeOf(b, p)?.final_arrive_t); return Number.isFinite(t) ? t - number(p.occur_t) : null; }
  const arrivalMean = (b) => num(b.metrics?.occur_to_final_arrival_mean ?? meanOutcomes(b, 'final_arrive_t'));
  const differs = (list, p) => new Set(list.map(b => finalHospital(b, p) ?? '')).size > 1;

  // First point where two policies chose a different destination for the same patient.
  function patientSplit(a, b, p) {
    const ha = assignmentHistory(a, p), hb = assignmentHistory(b, p);
    for (let i = 0; i < Math.max(ha.length, hb.length); i++) {
      if (ha[i]?.hospital === hb[i]?.hospital) continue;
      const t = Math.min(ha[i]?.t ?? Infinity, hb[i]?.t ?? Infinity);
      return Number.isFinite(t) ? { t, index: i, patient: p, a: ha[i]?.hospital ?? null, b: hb[i]?.hospital ?? null } : null;
    }
    return null;
  }
  function firstSplit(list) {
    const found = patients().map(p => {
      let best = null;
      for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
        const split = patientSplit(list[i], list[j], p);
        if (split && (!best || split.t < best.t)) best = split;
      }
      return best;
    }).filter(Boolean);
    if (!found.length) return null;
    const t = Math.min(...found.map(s => s.t));
    const at = found.filter(s => Math.abs(s.t - t) < 1e-7).sort((a, z) => interest(z.patient, list) - interest(a.patient, list));
    return { t, primary: at[0], others: at.slice(1).map(s => s.patient) };
  }
  // Among patients that split at the same moment, lead with the one left unassigned
  // somewhere, then one with a single compatible hospital, then higher priority.
  const interest = (p, list) => (list.some(b => !finalHospital(b, p)) ? 4 : 0) + (compatibleHospitals(p)?.length === 1 ? 2 : 0) + number(p.priority) / 10;
  function compatibleHospitals(p) {
    const need = p.required_capabilities ?? [];
    if (!need.length || !hospitals().some(h => Array.isArray(h.capabilities))) return null;
    return hospitals().filter(h => need.every(c => (h.capabilities ?? []).includes(c)));
  }
  const scarceCapabilities = (p) => (p.required_capabilities ?? []).filter(c => !hospitals().every(h => (h.capabilities ?? []).includes(c)));
  function scarcityFacts(p, list) {
    const compatible = compatibleHospitals(p);
    if (compatible?.length !== 1) return null;
    const only = compatible[0].id, caps = scarceCapabilities(p);
    const lead = `${shortPatient(p)}의 필요 역량${caps.length ? `(${caps.join(', ')})` : ''}을 갖춘 병원은 ${shortHospital(only)} 1곳입니다.`;
    const groups = groupBy(list.filter(b => finalHospital(b, p) !== only), b => {
      const first = b.events.find(e => typeOf(e) === 'commit' && hospitalId(e) === String(only) && patientId(e) !== String(p.id));
      return first ? `${patientId(first)}\u0000${when(first)}` : '';
    });
    const follow = [...groups].filter(([key]) => key).map(([key, bs]) => { const [pid, t] = key.split('\u0000'); return { branches: bs, patient: pid, t: Number(t), hospital: only }; });
    return { lead, follow };
  }

  // ---------- shared summary map drawing ----------
  // viewBox around the points; `top`/`bottom` keep a share of the height free for overlays.
  function fitFrame(points, aspect, { pad = .18, minSize = 60, top = 0, bottom = 0 } = {}) {
    const xy = points.filter(validLoc).map(p => project(p));
    if (!xy.length) return { x: 0, y: 0, w: 1000, h: 1000 / aspect };
    const xs = xy.map(p => p[0]), ys = xy.map(p => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const free = Math.max(.3, 1 - top - bottom);
    let w = Math.max(minSize, (maxX - minX) * (1 + pad * 2)), h = Math.max(minSize / aspect, (maxY - minY) * (1 + pad * 2)) / free;
    if (w / h > aspect) h = w / aspect; else w = h * aspect;
    return { x: (minX + maxX) / 2 - w / 2, y: (minY + maxY) / 2 - h * free / 2 - h * top, w, h };
  }
  function drawBase(target, frame, fill) {
    target.append(svg('rect', { x: frame.x - frame.w, y: frame.y - frame.h, width: frame.w * 3, height: frame.h * 3, fill }));
    if ($('lab-geo').childElementCount) target.append(svg('use', { href: '#lab-geo' }));
  }
  function buildGeography() {
    const holder = $('lab-geo');
    holder.replaceChildren();
    if (syntheticLocations() || !state.geography) return;
    (state.geography.features ?? []).forEach((f, i) => {
      const d = geometryPath(f.geometry);
      if (d) holder.append(svg('path', { d, fill: ['#E2E9F0', '#EBF0F5', '#E5ECF3', '#E8EEF4'][i % 4], stroke: '#C5D2DE', 'stroke-width': 1, 'fill-rule': 'evenodd', 'vector-effect': 'non-scaling-stroke' }));
    });
  }
  const line = (a, b, attrs) => svg('path', { d: `M${a[0].toFixed(2)},${a[1].toFixed(2)} L${b[0].toFixed(2)},${b[1].toFixed(2)}`, fill: 'none', 'stroke-linecap': 'round', ...attrs });
  function hospitalMark([x, y], k, strong) {
    const s = 11 * k;
    return svg('rect', { x: x - s / 2, y: y - s / 2, width: s, height: s, rx: 2.5 * k, fill: strong ? '#15202B' : '#fff', stroke: strong ? '#fff' : '#8C9BAB', 'stroke-width': 1.5 * k });
  }
  function textLabel([x, y], text, k, attrs = {}) {
    return svg('text', { x, y, 'font-size': 12.5 * k, 'font-weight': 700, fill: '#15202B', 'paint-order': 'stroke', stroke: '#fff', 'stroke-width': 3.2 * k, 'stroke-linejoin': 'round', ...attrs }, text);
  }
  function crossMark([x, y], k, color = '#C53030') {
    const r = 5 * k;
    return svg('path', { d: `M${x - r},${y - r} L${x + r},${y + r} M${x + r},${y - r} L${x - r},${y + r}`, stroke: color, 'stroke-width': 3 * k, 'stroke-linecap': 'round', fill: 'none' });
  }
  function origins(list) {
    const groups = groupBy(list.filter(p => validLoc(locOf(p))), p => locOf(p).map(v => Number(v).toFixed(4)).join(','));
    return [...groups.values()].map(ps => ({ point: project(locOf(ps[0])), patients: ps }));
  }
  // Nearby origins share one pin so their labels do not pile up.
  function clusterOrigins(list, radius) {
    const groups = [];
    origins(list).forEach(o => {
      const group = groups.find(g => Math.hypot(g.point[0] - o.point[0], g.point[1] - o.point[1]) < radius);
      if (group) group.patients.push(...o.patients); else groups.push({ point: o.point, patients: [...o.patients] });
    });
    return groups;
  }

  // ---------- 결과 비교 ----------
  function caseLabel() {
    const options = [...$('scenario-select').options];
    const index = options.findIndex(o => o.value === String(current().id));
    return index >= 0 ? `CASE ${index + 1} / ${options.length}` : '';
  }
  function renderCompare() {
    const list = branches(), scenario = current();
    $('compare-meta').textContent = [caseLabel(), scenario.seed != null ? `SEED ${scenario.seed}` : '', `관측 종료 ${clock(state.duration)} 기준`].filter(Boolean).join(' · ');
    $('compare-title').textContent = scenario.label ?? scenario.id ?? '결과 비교';
    $('compare-description').textContent = scenario.summary_description ?? scenario.description ?? '';
    renderMultiples(list);
    renderBars(list);
    renderWhy(list);
    renderDiffTable(list);
    atTime(state.duration, appRenderResults);
    dirty.compare = false;
  }

  function renderMultiples(list) {
    const box = $('compare-multiples');
    if (!patients().length) { box.replaceChildren(node('p', 'empty-note', '환자가 없는 기록이라 배정 지도를 그리지 않습니다. 아래 표에서 서버 처리 기록을 확인합니다.')); return; }
    const used = new Set(list.flatMap(b => patients().map(p => finalHospital(b, p)).filter(id => id && hospitalById(id))));
    const points = [...patients().map(locOf), ...[...used].map(hospitalLoc)];
    if (!used.size) points.push(...hospitals().map(locOf));
    const frame = fitFrame(points, 4 / 3);
    // Patients left unassigned somewhere come first, then higher experimental priority.
    const unassignedIn = (p) => list.filter(b => !finalHospital(b, p)).length;
    const differing = patients().filter(p => list.length > 1 && differs(list, p)).sort((a, z) => unassignedIn(z) - unassignedIn(a) || number(z.priority) - number(a.priority));
    // Size map symbols for the rendered card width (auto-fit grid, 168px minimum, 12px gap).
    const width = box.clientWidth, columns = Math.max(1, Math.min(list.length, Math.floor((width + 12) / 180)));
    const mapWidth = width ? (width - 12 * (columns - 1)) / columns - 26 : 220;
    multiplesWidth = width;
    box.replaceChildren(...list.map(b => miniCard(b, frame, used, differing, list.length, mapWidth)));
  }

  function miniCard(b, frame, used, differing, branchCount, mapWidth) {
    const card = node('article', 'mini-card'); card.style.setProperty('--branch-color', b.color);
    const head = node('header'); head.append(node('span', 'swatch'), node('span', '', `${b.displayId} · ${b.label}`));
    const map = svg('svg', { class: 'mini-map', viewBox: `${frame.x} ${frame.y} ${frame.w} ${frame.h}`, preserveAspectRatio: 'xMidYMid meet', role: 'img' });
    const k = frame.w / Math.max(120, mapWidth), many = patients().length > 6;
    drawBase(map, frame, '#EEF2F6');
    const assigned = new Map(), unassigned = [];
    patients().forEach(p => {
      const h = finalHospital(b, p);
      if (h && validLoc(hospitalLoc(h))) { if (!assigned.has(h)) assigned.set(h, []); assigned.get(h).push(p); } else unassigned.push(p);
    });
    used.forEach(id => { if (!assigned.has(id) && validLoc(hospitalLoc(id))) map.append(hospitalMark(project(hospitalLoc(id)), k, false)); });
    assigned.forEach((ps, id) => { const end = project(hospitalLoc(id)); ps.forEach(p => { if (validLoc(locOf(p))) map.append(line(project(locOf(p)), end, { stroke: b.color, 'stroke-width': 3 * k, opacity: .9 })); }); });
    origins(patients()).forEach(o => map.append(svg('circle', { cx: o.point[0], cy: o.point[1], r: 5.5 * k, fill: '#fff', stroke: '#15202B', 'stroke-width': 2 * k })));
    const describe = [];
    assigned.forEach((ps, id) => {
      const at = project(hospitalLoc(id));
      map.append(hospitalMark(at, k, true), textLabel([at[0] + 9 * k, at[1] - 7 * k], many ? `${ps.length}명` : ps.map(shortPatient).join('·'), k));
      describe.push(`${ps.map(shortPatient).join('·')} ${shortHospital(id)}`);
    });
    if (unassigned.length) {
      const spot = validLoc(locOf(unassigned[0])) ? project(locOf(unassigned[0])) : [frame.x + frame.w / 2, frame.y + frame.h / 2];
      const at = [spot[0] + 11 * k, spot[1] + 11 * k];
      map.append(crossMark(at, k), textLabel([at[0] + 8 * k, at[1] + 5 * k], many ? `미배정 ${unassigned.length}명` : unassigned.map(shortPatient).join('·'), k, { fill: '#9B1C1C' }));
      describe.push(`${unassigned.map(shortPatient).join('·')} 미배정`);
    }
    map.setAttribute('aria-label', `${b.displayId} 최종 배정: ${describe.join(', ')}`);
    map.append(svg('title', {}, `${b.displayId} · ${b.label}\n${describe.join('\n')}`));
    const stats = node('div', 'mini-stats');
    stats.append(node('span', 'mono', `확정 ${committedCount(b)}/${patients().length} · 문의 ${asks(b, state.duration)}`));
    if (branchCount > 1 && differing.length) {
      differing.slice(0, 2).forEach(p => { const h = finalHospital(b, p); stats.append(node('span', `chip ${h ? 'chip-ok' : 'chip-bad'}`, `${shortPatient(p)} ${h ? tinyHospital(h) : '미배정'}`)); });
      if (differing.length > 2) stats.append(node('span', 'chip chip-neutral', `외 ${differing.length - 2}명 다름`));
    } else if (unassigned.length) stats.append(node('span', 'chip chip-bad', unassigned.length <= 2 ? `${unassigned.map(shortPatient).join('·')} 미배정` : `미배정 ${unassigned.length}명`));
    else if (branchCount > 1) stats.append(node('span', 'chip chip-neutral', '모든 정책과 같은 배정'));
    card.append(head, map, stats);
    return card;
  }

  function renderBars(list) {
    const N = patients().length, box = $('compare-bars');
    const rows = [
      { label: '수용 확정', values: list.map(committedCount), format: v => `${v}/${N}`, max: N },
      { label: '문의 건수', values: list.map(b => asks(b, state.duration)), format: v => `${v}건` },
      { label: '관측 종료 시 미배정', values: list.map(b => patients().filter(p => !finalHospital(b, p)).length), format: v => `${v}명` }
    ];
    if (!isVoice()) rows.push({ label: serverCase() ? '예상 도착 평균' : '최종병원 도착 평균', values: list.map(arrivalMean), format: v => Number.isFinite(v) ? resultTime(v) : '—', sub: list.map(b => `도착 ${patients().filter(p => arrivalOf(b, p) != null).length}명`) });
    if (list.some(b => b.metrics?.retries != null)) rows.push({ label: '재문의', values: list.map(b => num(b.metrics?.retries)), format: v => Number.isFinite(v) ? `${v}건` : '—' });
    if (list.some(b => b.metrics?.travel_minutes != null)) rows.push({ label: '이송 이동시간 합계', values: list.map(b => num(b.metrics?.travel_minutes)), format: v => Number.isFinite(v) ? `${Math.round(v)}분` : '—' });
    if (priorityComparisonEnabled()) rows.push({ label: '4군 치료 미시작', values: atTime(state.duration, () => list.map(b => num(priorityUntreatedCounts(b)?.[4]))), format: v => Number.isFinite(v) ? `${v}명` : '—' });
    const table = node('table'), head = node('tr');
    head.append(node('th', '', '지표'));
    list.forEach(b => { const th = node('th', 'branch-ink', b.displayId); th.style.setProperty('--branch-color', b.color); th.append(node('small', '', b.label)); th.title = b.label; head.append(th); });
    const thead = node('thead'); thead.append(head);
    const tbody = node('tbody');
    rows.forEach(row => {
      const tr = node('tr'); tr.append(node('th', '', row.label));
      const finite = row.values.filter(Number.isFinite), max = row.max ?? Math.max(0, ...finite);
      row.values.forEach((v, i) => {
        const td = node('td'); td.style.setProperty('--branch-color', list[i].color);
        td.append(node('span', 'bar-value', row.format(v)));
        if (row.sub) td.append(node('span', 'bar-sub', row.sub[i]));
        if (Number.isFinite(v) && max > 0) { const bar = node('div', 'bar'), fill = node('i'); fill.style.width = `${Math.max(0, Math.min(100, 100 * v / max))}%`; bar.append(fill); td.append(bar); }
        tr.append(td);
      });
      tbody.append(tr);
    });
    table.append(thead, tbody);
    box.replaceChildren(table);
  }

  function rangeFact(label, list, values, format) {
    const groups = groupBy(list.map((b, i) => ({ b, v: values[i] })).filter(x => Number.isFinite(x.v)), x => x.v);
    if (groups.size < 2) return groups.size ? rich(`${label}: 모든 정책이 같음 (`, bold(format([...groups.keys()][0])), ')') : null;
    const keys = [...groups.keys()].sort((a, z) => a - z), low = groups.get(keys[0]).map(x => x.b), high = groups.get(keys.at(-1)).map(x => x.b);
    return rich(`${label}: `, branchTag(low), ' ', bold(format(keys[0])), ' ↔ ', branchTag(high), ' ', bold(format(keys.at(-1))));
  }

  function renderWhy(list) {
    const N = patients().length, items = [], splitBox = $('compare-first-split');
    $('why-title').textContent = list.length > 1 ? '왜 달라졌나' : '기록 요약';
    if (!N) {
      splitBox.textContent = '이 기록에는 환자가 없습니다. 서버가 입력을 받아들이지 않은 과정은 아래 전체 지표와 지도 재생의 사건 기록에서 봅니다.';
      $('compare-diff-summary').replaceChildren();
      return;
    }
    if (list.length < 2) {
      const b = list[0];
      splitBox.textContent = '정책이 하나뿐인 서버 처리 기록이라 정책 간 비교 대상이 없습니다.';
      items.push(rich('수용 확정 ', bold(`${committedCount(b)}/${N}명`), ' · 문의 ', bold(`${asks(b, state.duration)}건`)));
      const redirects = finalEvents(b).filter(e => typeOf(e) === 'redirect').length;
      if (redirects) items.push(rich('사람이 확정한 목적지 변경 ', bold(`${redirects}건`)));
      const open = patients().filter(p => !finalHospital(b, p));
      items.push(open.length ? rich('관측 종료 시 목적지 없음 — ', bold(open.map(shortPatient).join('·'))) : rich('모든 환자의 목적지가 기록됐습니다.'));
    } else {
      const split = firstSplit(list);
      if (split) {
        const p = split.primary.patient, idx = split.primary.index;
        const groups = groupBy(list, b => assignmentHistory(b, p)[idx]?.hospital ?? '');
        const parts = [];
        [...groups].forEach(([hid, bs], i) => { if (i) parts.push(' / '); parts.push(branchTag(bs), ` ${hid ? shortHospital(hid) : '미확정'}`); });
        splitBox.replaceChildren(rich('처음 갈린 시각 ', bold(clock(split.t)), ` · ${shortPatient(p)} — `, ...parts, split.others.length ? ` (같은 시각 ${split.others.map(shortPatient).join('·')}도 갈림)` : ''));
      } else splitBox.textContent = list.some(b => committedCount(b) > 0) ? '모든 정책이 같은 순서로 같은 병원을 확정했습니다.' : '어느 정책도 관측 종료까지 수용을 확정하지 못했습니다.';
      const differing = patients().filter(p => differs(list, p));
      items.push(differing.length
        ? rich('결과가 갈린 환자 ', bold(`${differing.length}명`), ` / 전체 ${N}명 — ${idList(differing)}`)
        : rich(`모든 정책이 환자 ${N}명을 같은 병원에 배정했습니다.`));
      const unassignedGroups = groupBy(list, b => patients().filter(p => !finalHospital(b, p)).map(p => String(p.id)).join('\u0000'));
      [...unassignedGroups].filter(([ids]) => ids).forEach(([ids, bs]) => { const open = ids.split('\u0000'); items.push(rich(branchTag(bs), ` 관측 종료까지 미배정 ${open.length}명 — `, bold(idList(open)))); });
      if (![...unassignedGroups.keys()].some(Boolean)) items.push(rich('모든 정책이 관측 종료 전에 전원 배정했습니다.'));
      differing.map(p => ({ p, facts: scarcityFacts(p, list) })).filter(x => x.facts).slice(0, 2).forEach(({ facts }) => {
        items.push(rich(facts.lead, ...facts.follow.map(f => [' ', branchTag(f.branches), `에서는 ${clock(f.t)}에 이 병원이 먼저 다른 환자(${shortPatient(f.patient)})에게 확정됐습니다.`])));
      });
      const inquiry = rangeFact('문의 건수', list, list.map(b => asks(b, state.duration)), v => `${v}건`);
      if (inquiry) items.push(inquiry);
      if (list.some(b => b.metrics?.retries != null)) { const retry = rangeFact('재문의', list, list.map(b => num(b.metrics?.retries)), v => `${v}건`); if (retry && new Set(list.map(b => b.metrics?.retries)).size > 1) items.push(retry); }
      if (!isVoice()) {
        const values = list.map(arrivalMean);
        if (new Set(values.filter(Number.isFinite)).size > 1) {
          const fact = rangeFact(serverCase() ? '예상 도착 평균' : '최종병원 도착 평균', list, values, v => resultTime(v));
          items.push(rich(fact, '. 도착한 환자만 평균하므로 미배정 수와 함께 봅니다.'));
        }
      }
      if (priorityComparisonEnabled()) {
        const counts = atTime(state.duration, () => list.map(b => num(priorityUntreatedCounts(b)?.[4])));
        if (new Set(counts.filter(Number.isFinite)).size > 1) items.push(rangeFact('4군 치료 미시작', list, counts, v => `${v}명`));
      }
    }
    $('compare-diff-summary').replaceChildren(...items.map(item => { const li = node('li'); li.append(item); return li; }));
  }

  function renderDiffTable(list) {
    const table = $('compare-diff-table'), head = node('tr');
    head.append(node('th', '', '환자'));
    list.forEach(b => { const th = node('th', 'branch-ink', b.displayId); th.style.setProperty('--branch-color', b.color); th.title = b.label; head.append(th); });
    table.tHead.replaceChildren(head);
    const rows = patients().map(p => ({ p, hospitals: list.map(b => finalHospital(b, p)), diff: list.length > 1 && differs(list, p) }));
    const ordered = [...rows.filter(r => r.diff), ...rows.filter(r => !r.diff)];
    table.tBodies[0].replaceChildren(...ordered.map(r => {
      const tr = node('tr', r.diff ? 'is-diff' : ''), th = node('th', '', shortPatient(r.p));
      th.title = `${r.p.id} · ${r.p.label ?? '가상 환자'}`;
      if (r.diff) th.append(node('span', 'diff-tag', '다름'));
      tr.append(th);
      r.hospitals.forEach((h, i) => {
        const td = node('td', h ? '' : 'unassigned', h ? shortHospital(h) : '미배정'), arrival = arrivalOf(list[i], r.p);
        if (h && arrival != null && !isVoice()) td.append(node('small', '', `${serverCase() ? '예상 도착' : '도착'} ${resultTime(arrival)}`));
        tr.append(td);
      });
      return tr;
    }));
    if (!rows.length) table.tBodies[0].replaceChildren((() => { const tr = node('tr'), td = node('td', '', '환자 기록이 없습니다.'); td.colSpan = list.length + 1; tr.append(td); return tr; })());
    $('compare-diff-count').textContent = list.length > 1 ? `다른 배정 ${rows.filter(r => r.diff).length}명 / ${rows.length}명` : `${rows.length}명`;
  }

  // ---------- 발표 ----------
  function defaultPair(list) {
    if (list.length < 2) return [list[0]?.id ?? null, list[0]?.id ?? null];
    const assigned = list.map(b => patients().filter(p => finalHospital(b, p)).length);
    const min = Math.min(...assigned), max = Math.max(...assigned);
    if (min !== max) return [assigned.indexOf(min), assigned.lastIndexOf(max)].sort((a, z) => a - z).map(i => list[i].id);
    let best = [list[0], list.at(-1)], most = 0;
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      const count = patients().filter(p => finalHospital(list[i], p) !== finalHospital(list[j], p)).length;
      if (count > most) { most = count; best = [list[i], list[j]]; }
    }
    return best.map(b => b.id);
  }
  function pairSplit(a, b) {
    const found = firstSplit([a, b]);
    return found ? { ...found.primary, others: found.others } : null;
  }
  function linesAt(list, t, focusId) {
    const out = [];
    list.forEach((b, side) => patients().forEach(p => {
      const last = assignmentHistory(b, p).filter(x => x.t <= t + 1e-7).at(-1);
      if (last && validLoc(hospitalLoc(last.hospital))) out.push({ patient: p, hospital: last.hospital, color: b.color, side, strong: focusId == null || String(p.id) === String(focusId), branch: b });
    }));
    return out;
  }

  function buildSteps(L, R) {
    const P = patients(), N = P.length, single = !R || L === R, pair = single ? [L] : [L, R];
    if (!L || !N) return [{ when: '', title: '환자 없이 끝난 기록', body: current().summary_description ?? current().description ?? '이 기록에는 환자가 없습니다.', caption: null, layers: {} }];
    const steps = [], H = hospitals().length;
    const occurT = Math.min(...P.map(p => number(p.occur_t)));
    const sameSpot = N > 1 && origins(P).length === 1;
    steps.push({ when: clock(occurT), title: recordedCase() ? '환자 정보 접수' : '환자 발생', body: `${sameSpot ? '같은 지점에서 ' : ''}가상 환자 ${N}명${recordedCase() ? '의 정보가 접수됩니다' : '이 발생합니다'}. 후보 병원은 ${H}곳입니다.`, caption: { big: `${N}명`, text: `가상 환자 · 병원 ${H}곳` }, layers: {} });
    if (!recordedCase() && P.some(p => p.ambulance_arrive_t != null)) {
      const arriveT = Math.min(...P.map(p => num(p.ambulance_arrive_t)).filter(Number.isFinite));
      const searchT = Math.min(...P.map(p => num(p.search_start_t)).filter(Number.isFinite));
      steps.push({ when: clock(arriveT), title: '119 현장 도착', body: `119가 ${clock(arriveT)}에 현장에 도착해 평가합니다.${Number.isFinite(searchT) ? ` 병원 탐색은 ${clock(searchT)}부터입니다.` : ''}${single ? '' : ' 여기까지는 두 정책이 같습니다.'}`, caption: { big: clock(arriveT), text: '119 현장 도착' }, layers: { dispatch: true } });
    }
    const split = single ? null : pairSplit(L, R);
    const commits = pair.flatMap(b => b.events.filter(e => typeOf(e) === 'commit'));
    const firstCommitT = commits.length ? Math.min(...commits.map(when)) : NaN;
    const askEvents = pair.flatMap(b => b.events.filter(e => typeOf(e) === 'ask'));
    if (askEvents.length) {
      const firstAskT = Math.min(...askEvents.map(when));
      const until = split?.t ?? (Number.isFinite(firstCommitT) ? firstCommitT : state.duration);
      const aL = asks(L, until), aR = single ? aL : asks(R, until), same = aL === aR;
      const seenPairs = new Set(), pairs = [];
      askEvents.filter(e => when(e) <= until + 1e-7).forEach(e => { const key = `${patientId(e)}|${hospitalId(e)}`; if (!seenPairs.has(key)) { seenPairs.add(key); pairs.push({ patient: patientId(e), hospital: hospitalId(e) }); } });
      steps.push({
        when: clock(firstAskT), title: '병원 문의 시작',
        body: `${clock(firstAskT)}부터 병원에 수용을 문의합니다. ${single ? `${clock(until)}까지 ${aL}건을 문의했습니다.` : same ? `${clock(until)}까지는 두 정책 모두 ${aL}건으로 같습니다.` : `${clock(until)}까지 ${L.displayId} ${aL}건, ${R.displayId} ${aR}건입니다.`}`,
        caption: { big: same ? `${aL}건` : `${aL} · ${aR}건`, text: single ? '문의' : same ? '문의 · 두 정책이 같음' : `문의 · ${L.displayId} / ${R.displayId}` },
        layers: { asks: pairs }
      });
    }
    if (split) {
      const p = split.patient, facts = scarcityFacts(p, pair);
      const follow = facts?.follow.map(f => ` ${f.branches.map(b => b.displayId).join('·')}에서는 ${clock(f.t)}에 이 병원이 먼저 다른 환자(${shortPatient(f.patient)})에게 확정됐습니다.`).join('') ?? '';
      const target = (b, hid) => hid ? shortHospital(hid) : finalHospital(b, p) ? '아직 미확정' : '확정 없음(관측 종료까지)';
      steps.push({
        when: clock(split.t), title: '처음 갈린 순간',
        body: `${clock(split.t)}, ${patientName(p)}의 목적지가 갈립니다 — ${L.displayId} → ${target(L, split.a)}, ${R.displayId} → ${target(R, split.b)}.${facts ? ` ${facts.lead}${follow}` : ''}${split.others.length ? ` 같은 시각 ${split.others.map(shortPatient).join('·')}의 목적지도 갈립니다.` : ''}`,
        caption: { big: clock(split.t), text: `처음 갈림 · ${shortPatient(p)}` },
        layers: { lines: linesAt(pair, split.t, p.id), focus: [split.a, split.b].filter(Boolean), labelPatient: p, unassigned: pair.map((b, side) => ({ patient: p, color: b.color, side, branch: b, label: '미확정' })).filter((x, side) => !(side ? split.b : split.a)) }
      });
    } else if (Number.isFinite(firstCommitT)) {
      const first = commits.find(e => Math.abs(when(e) - firstCommitT) < 1e-7);
      steps.push({
        when: clock(firstCommitT), title: single ? '첫 수용 확정' : '병원 선택이 같음',
        body: `${single ? '' : '두 정책이 같은 순서로 같은 병원을 확정합니다. '}첫 확정은 ${clock(firstCommitT)} ${patientName(patientId(first))} → ${shortHospital(hospitalId(first))}입니다.`,
        caption: { big: clock(firstCommitT), text: '첫 수용 확정' },
        layers: { lines: linesAt(pair, firstCommitT), focus: [hospitalId(first)] }
      });
    }
    // A single server record: show the human-confirmed destination change, if any.
    const redirect = single ? L.events.find(e => typeOf(e) === 'redirect' && hospitalId(e)) : null;
    if (redirect) {
      const p = P.find(x => String(x.id) === patientId(redirect)), before = assignmentHistory(L, p ?? { id: patientId(redirect) }).filter(x => x.t < when(redirect) - 1e-7).at(-1);
      steps.push({
        when: clock(when(redirect)), title: '목적지 변경',
        body: `${clock(when(redirect))}, ${patientName(patientId(redirect))}의 목적지 변경이 확정됩니다 — ${before ? `${shortHospital(before.hospital)} → ` : ''}${shortHospital(hospitalId(redirect))}.${transportCase() ? ' 대체 수용 응답만으로는 바뀌지 않고 사람이 변경한 기록입니다.' : ''}`,
        caption: { big: clock(when(redirect)), text: '목적지 변경 확정' },
        layers: { lines: linesAt(pair, when(redirect)), focus: [hospitalId(redirect), before?.hospital].filter(Boolean) }
      });
    }
    const finals = [], unassigned = [];
    pair.forEach((b, side) => P.forEach(p => { const h = finalHospital(b, p); if (h && validLoc(hospitalLoc(h))) finals.push({ patient: p, hospital: h, color: b.color, side, strong: true, branch: b }); else unassigned.push({ patient: p, color: b.color, side, branch: b }); }));
    const summary = pair.map(b => { const open = P.filter(p => !finalHospital(b, p)); return `${single ? '' : `${b.displayId} `}확정 ${committedCount(b)}/${N}명${open.length ? `(미배정 ${idList(open, 6)})` : ''}`; }).join(', ');
    steps.push({ when: clock(state.duration), title: '결과', body: `관측 종료(${clock(state.duration)}) 기준 ${summary}.`, caption: null, layers: { lines: finals, unassigned }, results: true });
    return steps;
  }

  function renderStory() {
    const list = branches();
    if (!list.some(b => b.id === story.left) || !list.some(b => b.id === story.right)) { [story.left, story.right] = defaultPair(list); story.step = 0; }
    const options = () => list.map(b => { const o = node('option', '', `${b.displayId} · ${b.label}`); o.value = b.id; return o; });
    $('story-left').replaceChildren(...options()); $('story-right').replaceChildren(...options());
    $('story-left').value = story.left ?? ''; $('story-right').value = story.right ?? '';
    $('story-pair').hidden = list.length < 2;
    const L = list.find(b => b.id === story.left), R = list.find(b => b.id === story.right), single = !R || L === R;
    story.steps = buildSteps(L, R);
    story.step = Math.min(story.step, story.steps.length - 1);
    const pair = single ? [L].filter(Boolean) : [L, R];
    const differing = !single && patients().some(p => finalHospital(L, p) !== finalHospital(R, p));
    $('story-title').textContent = !patients().length ? '기록 단계별 보기' : single ? '한 기록, 단계별로' : differing ? '같은 환자, 다른 배정' : '같은 환자, 같은 배정';
    $('story-lead').textContent = patients().length
      ? `가상 환자 ${patients().length}명 · 병원 ${hospitals().length}곳. ${single ? '한 기록의 처리 과정을' : '두 정책이 어디서 갈리는지'} ${story.steps.length}단계로 봅니다.`
      : '';
    const involved = new Set();
    pair.forEach(b => b.events.forEach(e => { if ((typeOf(e) === 'ask' || ASSIGN_TYPES.has(typeOf(e))) && hospitalId(e)) involved.add(hospitalId(e)); }));
    pair.forEach(b => patients().forEach(p => { const h = finalHospital(b, p); if (h) involved.add(h); }));
    story.involved = [...involved].filter(id => validLoc(hospitalLoc(id)));
    story.pair = pair;
    renderStepList();
    renderStoryStep();
    dirty.story = false;
  }

  function renderStepList() {
    $('story-step-list').replaceChildren(...story.steps.map((s, i) => {
      const li = node('li'), button = node('button', 'story-step'); button.type = 'button'; button.dataset.step = i;
      const copy = node('span', 'copy');
      copy.append(node('span', 'when', s.when), node('span', 'title', s.title), node('span', 'body', s.body));
      button.append(node('span', 'num', String(i + 1)), copy);
      button.addEventListener('click', () => goStep(i));
      li.append(button);
      return li;
    }));
  }

  function renderStoryStep() {
    const step = story.steps[story.step];
    document.querySelectorAll('#story-step-list .story-step').forEach((button, i) => {
      button.classList.toggle('is-done', i < story.step);
      if (i === story.step) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
      button.querySelector('.body').hidden = i !== story.step || !story.steps[i].body;
    });
    $('story-prev').disabled = story.step <= 0;
    $('story-next').disabled = story.step >= story.steps.length - 1;
    $('story-counter').textContent = `${story.step + 1} / ${story.steps.length}`;
    const caption = $('story-caption');
    caption.replaceChildren();
    if (step?.caption) caption.append(node('strong', '', step.caption.big), node('span', '', step.caption.text));
    const results = $('story-results');
    results.hidden = !step?.results;
    if (step?.results) results.replaceChildren(...story.pair.map(b => {
      const card = node('article', 'result-card'); card.style.setProperty('--branch-color', b.color);
      const open = patients().filter(p => !finalHospital(b, p)), mean = arrivalMean(b);
      const items = node('ul');
      items.append(node('li', '', `문의 ${asks(b, state.duration)}건`), node('li', '', open.length ? `미배정 ${open.length}명 · ${idList(open, 5)}` : '미배정 없음'));
      if (!isVoice() && Number.isFinite(mean)) items.append(node('li', '', `${serverCase() ? '예상 도착' : '도착'} 평균 ${resultTime(mean)} · ${patients().filter(p => arrivalOf(b, p) != null).length}명`));
      card.append(node('h3', '', `${b.displayId} · ${b.label}`), node('span', 'big', `${committedCount(b)}/${patients().length} 확정`), items);
      return card;
    }));
    drawStoryMap();
  }

  function drawStoryMap() {
    const map = $('story-map'), step = story.steps[story.step];
    if (!step) return;
    const box = map.getBoundingClientRect(), aspect = box.width > 0 && box.height > 0 ? box.width / box.height : 1.45;
    const P = patients(), withDispatch = story.steps.some(s => s.layers.dispatch);
    const points = [...P.map(locOf), ...story.involved.map(hospitalLoc), ...(withDispatch ? P.map(p => p.ambulance_origin).filter(validLoc) : [])];
    if (!story.involved.length) points.push(...hospitals().map(locOf));
    // Keep the frame fixed across steps: room for the caption (top) and result cards (bottom).
    const overlay = window.matchMedia('(min-width: 641px)').matches && box.height > 0;
    const frame = fitFrame(points, aspect, { pad: .16, top: overlay ? Math.min(.25, 96 / box.height) : .05, bottom: overlay && story.steps.some(s => s.results) ? Math.min(.38, 200 / box.height) : .07 });
    const k = frame.w / Math.max(320, box.width || 900);
    map.setAttribute('viewBox', `${frame.x} ${frame.y} ${frame.w} ${frame.h}`);
    map.replaceChildren();
    drawBase(map, frame, '#E9EFF5');
    if (!syntheticLocations()) REGION_LABELS.forEach(l => { const [x, y] = project([l.lat, l.lon]); if (x > frame.x && x < frame.x + frame.w && y > frame.y && y < frame.y + frame.h) map.append(svg('text', { x, y, 'text-anchor': 'middle', 'font-size': 22 * k, 'font-weight': 600, 'letter-spacing': 6 * k, fill: '#A9B7C6', opacity: .8 }, l.name)); });
    const layers = step.layers ?? {}, focus = new Set((layers.focus ?? []).map(String));
    const patientPoint = (id) => { const p = P.find(x => String(x.id) === String(id)); return p && validLoc(locOf(p)) ? project(locOf(p)) : null; };
    if (layers.dispatch) P.forEach(p => { if (!validLoc(p.ambulance_origin) || !validLoc(locOf(p))) return; const a = project(p.ambulance_origin), b = project(locOf(p)); map.append(line(a, b, { stroke: '#5B7088', 'stroke-width': 2.2 * k, 'stroke-dasharray': `${6 * k} ${5 * k}`, opacity: .8 }), svg('rect', { x: a[0] - 5 * k, y: a[1] - 5 * k, width: 10 * k, height: 10 * k, rx: 2 * k, fill: '#5B7088' })); });
    (layers.asks ?? []).forEach(pair => { const a = patientPoint(pair.patient), loc = hospitalLoc(pair.hospital); if (a && validLoc(loc)) map.append(line(a, project(loc), { stroke: '#1F3A5F', 'stroke-width': 2 * k, 'stroke-dasharray': `${3 * k} ${7 * k}`, opacity: .55 })); });
    (layers.lines ?? []).forEach(item => {
      const a = patientPoint(item.patient.id), b = project(hospitalLoc(item.hospital));
      if (!a) return;
      const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1, off = (item.side ? 1 : -1) * 2.6 * k * (story.pair.length > 1 ? 1 : 0);
      const shift = ([x, y]) => [x - dy / len * off, y + dx / len * off];
      map.append(line(shift(a), shift(b), { stroke: item.color, 'stroke-width': (item.strong ? 4.5 : 2.4) * k, opacity: item.strong ? .95 : .3 }));
    });
    story.involved.forEach(id => { const at = project(hospitalLoc(id)), strong = focus.has(String(id)); map.append(hospitalMark(at, k * 1.3, strong), textLabel([at[0] + 11 * k, at[1] - 9 * k], shortHospital(id), k, { 'font-size': 15 * k, 'font-weight': strong ? 800 : 600, fill: strong ? '#121A23' : '#34414F' })); });
    if (layers.labelPatient) (layers.lines ?? []).filter(item => item.strong).forEach(item => {
      const at = project(hospitalLoc(item.hospital));
      map.append(textLabel([at[0] + 11 * k, at[1] + (item.side ? 26 : 10) * k], `${item.branch.displayId} · ${shortPatient(item.patient)}`, k, { fill: item.color, 'font-size': 14 * k, 'font-weight': 800 }));
    });
    clusterOrigins(P, 30 * k).forEach(o => {
      const [x, y] = o.point;
      map.append(svg('circle', { cx: x, cy: y, r: 18 * k, fill: '#DF4B21', opacity: .14 }), svg('circle', { cx: x, cy: y, r: 8 * k, fill: '#DF4B21', stroke: '#fff', 'stroke-width': 2.5 * k }));
      map.append(textLabel([x, y - 16 * k], o.patients.length > 1 ? `환자 ${o.patients.length}명` : shortPatient(o.patients[0]), k, { 'text-anchor': 'middle', 'font-size': 14 * k, fill: '#A52F10' }));
    });
    // One cross per policy; several unassigned patients collapse into a count.
    groupBy(layers.unassigned ?? [], item => item.side).forEach(items => {
      const item = items[0], base = patientPoint(item.patient.id); if (!base) return;
      const at = [base[0] + (item.side ? 18 : -18) * k, base[1] + 18 * k];
      const who = items.length > 1 ? `${item.label ?? '미배정'} ${items.length}명` : `${shortPatient(item.patient)} ${item.label ?? '미배정'}`;
      map.append(crossMark(at, k * 1.2), textLabel([at[0] + (item.side ? 10 : -10) * k, at[1] + 5 * k], `${story.pair.length > 1 ? `${item.branch.displayId} ` : ''}${who}`, k, { fill: '#9B1C1C', 'text-anchor': item.side ? 'start' : 'end', 'font-size': 13.5 * k }));
    });
    map.setAttribute('aria-label', `${step.title}: ${step.body}`);
    $('story-note').textContent = syntheticLocations() ? '합성 좌표 위의 요약도입니다. 선은 직선 요약이며 실제 지리를 재현하지 않습니다.' : '행정경계와 병원 위치 위의 요약도입니다. 선은 직선 요약이며 실제 도로 경로는 지도 재생에서 봅니다.';
  }

  function goStep(index) {
    if (!story.steps.length) return;
    story.step = Math.max(0, Math.min(story.steps.length - 1, index));
    renderStoryStep();
  }

  // ---------- view switching and app.js hooks ----------
  function ensureRendered(which) {
    if (!state.selected) return;
    if (dirty.geo && which !== 'replay') { buildGeography(); dirty.geo = false; }
    if (which === 'compare' && dirty.compare) renderCompare();
    if (which === 'story' && dirty.story) renderStory();
  }
  function setView(next, { updateUrl = true } = {}) {
    if (!VIEWS.includes(next)) next = 'replay';
    if (next !== 'replay' && state.playing) { state.playing = false; render(); }
    view = next;
    document.body.dataset.view = next;
    document.querySelectorAll('.topnav [role="tab"]').forEach(tab => { const on = tab.dataset.view === next; tab.setAttribute('aria-selected', String(on)); tab.tabIndex = on ? 0 : -1; });
    VIEWS.forEach(v => { $(`view-${v}`).hidden = v !== next; });
    if (updateUrl) { const url = new URL(location.href); url.hash = next === 'replay' ? '' : next; history.replaceState(null, '', url); }
    if (next === 'replay') { if (state.selected) { renderMap(); renderScale(); scheduleBasemap(); } }
    else ensureRendered(next);
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  function afterRender() {
    $('map-clock-time').textContent = clock(state.time);
    $('map-clock-phase').textContent = $('playback-state').textContent;
    $('map-clock-label').textContent = isVoice() ? '사건 순서' : recordedCase() ? '접수 후' : '경과 시간';
    document.querySelector('.map-clock').dataset.playing = String(state.playing);
    $('analysis-heading').hidden = ['resource-section', 'inquiry-section', 'transport-section'].every(id => $(id).hidden);
  }

  const appRender = window.render;
  window.render = function labRender() { appRender(); afterRender(); };
  // The full metric table lives in 결과 비교 and always shows end-of-observation values there.
  const appRenderResults = window.renderResults;
  window.renderResults = function labRenderResults() { if (view === 'compare') atTime(state.duration, appRenderResults); };
  const appSelectScenario = window.selectScenario;
  let selecting = false;
  window.selectScenario = function labSelectScenario(id) {
    let ok;
    selecting = true;
    try { ok = appSelectScenario(id); } finally { selecting = false; }
    if (!ok) return ok;
    dirty.geo = dirty.compare = dirty.story = true;
    story.left = story.right = null; story.step = 0;
    const total = caseLabel();
    if (total) $('scenario-total').textContent = total;
    const url = new URL(location.href); url.searchParams.set('scenario', current().id); history.replaceState(null, '', url);
    if (view !== 'replay') ensureRendered(view);
    return ok;
  };
  const appRenderGeography = window.renderGeography;
  window.renderGeography = function labRenderGeography() {
    appRenderGeography();
    dirty.geo = dirty.compare = dirty.story = true;
    if (!selecting && view !== 'replay') ensureRendered(view);
  };
  window.replayDebug.selectScenario = window.selectScenario;
  window.replayDebug.setView = setView;
  Object.defineProperty(window.replayDebug, 'view', { get: () => view });
  Object.defineProperty(window.replayDebug, 'story', { get: () => ({ left: story.left, right: story.right, step: story.step, steps: story.steps.map(s => ({ when: s.when, title: s.title, body: s.body })) }) });

  document.querySelectorAll('.topnav [role="tab"]').forEach(tab => tab.addEventListener('click', () => setView(tab.dataset.view)));
  document.querySelector('.topnav').addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const tabs = [...document.querySelectorAll('.topnav [role="tab"]')], index = tabs.findIndex(t => t.dataset.view === view);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (index + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    e.preventDefault(); e.stopPropagation();
    setView(tabs[next].dataset.view); tabs[next].focus();
  });
  document.querySelectorAll('[data-goto]').forEach(button => button.addEventListener('click', () => setView(button.dataset.goto)));
  document.querySelector('.brand').addEventListener('click', () => setView('replay'));
  $('story-prev').addEventListener('click', () => goStep(story.step - 1));
  $('story-next').addEventListener('click', () => goStep(story.step + 1));
  const changePair = () => { story.left = $('story-left').value; story.right = $('story-right').value; renderStory(); };
  $('story-left').addEventListener('change', changePair);
  $('story-right').addEventListener('change', changePair);
  // Outside 지도 재생 the replay shortcuts (Space, ←/→) must not drive the hidden map;
  // in 발표 ←/→ and PageUp/PageDown (presentation clickers) move between steps.
  window.addEventListener('keydown', e => {
    if (view === 'replay' || document.activeElement?.closest?.('.topnav')) return;
    if (['Space', 'ArrowLeft', 'ArrowRight'].includes(e.code)) e.stopImmediatePropagation();
    const tag = document.activeElement?.tagName;
    if (view !== 'story' || ['INPUT', 'SELECT', 'TEXTAREA'].includes(tag) || e.altKey || e.ctrlKey || e.metaKey) return;
    const forward = ['ArrowRight', 'PageDown'].includes(e.key), back = ['ArrowLeft', 'PageUp'].includes(e.key);
    if (!forward && !back && !(tag !== 'BUTTON' && ['Home', 'End'].includes(e.key))) return;
    e.preventDefault();
    goStep(e.key === 'Home' ? 0 : e.key === 'End' ? story.steps.length - 1 : story.step + (forward ? 1 : -1));
  }, true);
  window.addEventListener('hashchange', () => setView(location.hash.replace('#', ''), { updateUrl: false }));
  new ResizeObserver(() => { if (view === 'story' && story.steps.length) drawStoryMap(); }).observe(document.querySelector('.story-stage'));
  new ResizeObserver(() => { if (view === 'compare' && !dirty.compare && Math.abs($('compare-multiples').clientWidth - multiplesWidth) > 40) renderMultiples(branches()); }).observe($('compare-multiples'));
  setView(location.hash.replace('#', '') || 'replay', { updateUrl: false });
})();
