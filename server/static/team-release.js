const status = document.getElementById('status');
const details = document.getElementById('details');
document.getElementById('patient-form').addEventListener('submit', event => {
  event.preventDefault();
  const id = document.getElementById('patient-id').value.trim();
  if (id) location.assign(`/patient/?pid=${encodeURIComponent(id)}`);
});
try {
  const response = await fetch('/api/team-runtime', {cache:'no-store'});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const info = await response.json();
  const release = info.release || {};
  status.textContent = release.verified ? '기동 시 공통 실행본 파일 확인 완료' : '개발 작업사본 — 확정 실행 묶음이 아닙니다';
  const rows = [
    ['실행본', release.label || release.status || 'unpackaged'],
    ['파일 명세 SHA-256', release.manifest_sha256 || '없음'],
    ['실행 모드', info.mode],
    ['문의 / 배정 워커', `${info.workers?.inquiry?.state || '미확인'} / ${info.workers?.planning?.state || '미확인'}`],
    ['DB', info.database?.path || '미확인'],
    ['자원 상태', `오래된 정보 ${info.database?.resources?.stale_count ?? '?'}개`],
    ['환경', `Python ${info.profile?.actual_environment?.python || '미확인'} / SQLite ${info.profile?.actual_environment?.sqlite || '미확인'}`]
  ];
  for (const [name, value] of rows) {
    const term = document.createElement('dt'); term.textContent = name;
    const description = document.createElement('dd'); description.textContent = String(value ?? '미확인');
    details.append(term, description);
  }
} catch (error) {
  status.textContent = `공통 실행 상태를 읽지 못했습니다: ${error.message}`;
  status.className = 'error';
}
