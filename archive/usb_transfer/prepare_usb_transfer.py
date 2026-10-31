"""Build a non-destructive, portable project handoff in a local staging folder."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import shutil
import json

root = Path(__file__).resolve().parent
stage = root / '.usb_export' / 'Capstone-20260923'
stage.mkdir(parents=True, exist_ok=True)
for p in root.iterdir():
    if p.is_file() and p.suffix.lower() in {'.py', '.md', '.docx', '.zip', '.json'}:
        shutil.copy2(p, stage / p.name)
for name in ('prototype', 'optimization_results', 'optimization_results_v2'):
    shutil.copytree(root / name, stage / name, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

readme = '''# USB에서 다른 PC로 작업 이어가기

이 폴더 전체를 다른 PC의 짧은 경로(예: C:\\Capstone)로 복사한 뒤 작업하세요.
USB 원본은 백업으로 두는 편이 좋습니다. 기존 PC의 작업 폴더는 삭제하지 않았습니다.

## 바로 볼 파일

- 캡스톤_프로젝트_제안서_멘토사전공유.docx: 멘토에게 보낼 2쪽 제안서
- 응급환자_이송체계_문제점과_개선방안_v2.docx: 7쪽 상세 개선 문서
- prototype/index.html: 더블클릭하면 실행되는 독립 사용성 화면
- 개선판_README.md: 알고리즘 입력, 구현 범위, 검증 결과
- HANDOFF.md: 다른 PC의 코딩 도우미에게 전달할 작업 맥락

## Windows에서 인터넷 없이 솔버 실행

1. 폴더 전체를 PC로 복사합니다.
2. SETUP_WINDOWS.cmd를 실행해 번들 Python 및 OR-Tools를 풉니다.
3. TEST_WINDOWS.cmd로 Python 테스트와 화면 상태 테스트를 실행합니다.
4. RUN_EXPERIMENTS_WINDOWS.cmd로 예시와 40회 가상 실험을 다시 실행합니다.

portable-runtime.zip은 Windows x64 Python 3.12, 기존 OR-Tools 라이브러리와 Node 실행 파일입니다.
약 700MB 이상의 추가 압축 해제 공간이 필요합니다. ARM/macOS/Linux용 런타임은 아닙니다.
운영체제 호환성은 대상 PC에서 확인해야 합니다. 다른 OS에서는 해당 OS용 Python과
requirements-portable.txt의 패키지를 설치하세요. HTML 화면과 문서는 별도 사용 가능합니다.

## 문서 재생성

번들 Python의 python-docx로 build_mentor_proposal.py 등을 실행할 수 있습니다.
build_improvements.py는 기존 조사 문서와 optimization_results_v2의 결과를 사용합니다.
문서 레이아웃 검사는 대상 PC의 Word 또는 별도 DOCX 렌더러가 필요합니다.
prototype/check_browser.cjs는 원래 PC의 Playwright와 Edge 절대 경로를 사용하는
개발자용 검사이므로 다른 PC에서는 경로와 의존성을 조정해야 합니다.

## 복사 검증

TRANSFER_MANIFEST.json에 파일별 SHA-256이 있습니다. 자동 전달 과정에서 USB 파일과
비교합니다. 문서와 소스는 일반 폴더로, 실행 환경은 별도 ZIP으로 보관했습니다.
Git 내부 이력, 임시 렌더링 PDF·이미지, Python 캐시는 제외했습니다.
Codex 계정·로그인·전체 채팅 이력은 포함되지 않으며 HANDOFF.md가 맥락을 요약합니다.
'''
(stage / 'USB_시작안내.md').write_text(readme, encoding='utf-8')
(stage / 'HANDOFF.md').write_text('''# 캡스톤 작업 인계

## 목표와 방향
여러 응급환자의 치료 요구, 병원 자원과 이동시간을 고려한 목적 병원 배정 지원 시스템.
교수님 제출과 Google Cloud 김영선 멘토 킥오프 준비 중. 멘토 제안 일정은 2026년
9월 26일 20시 이후 또는 27일 20시이며 팀의 확정 회신은 아직 기록되지 않았음.
최신 멘토 제안서는 캡스톤_프로젝트_제안서_멘토사전공유.docx.

## 사용자 선호
한국어로 간결하고 쉬운 설명. 초기에 가볍게 제출할 제안서를 원했음.
할 수 있는 작업은 직접 진행하고 불필요한 확인 질문은 줄일 것.
보고서에서 구현 완료, 가상 검증, 향후 계획을 구분할 것.

## 현재 완료한 작업
- 기존 제안서, 현황 조사 문서, 솔버 실험 문서 및 코드 보관.
- solver_improved.py: 계층적 목적함수 2정책, 최초 기준시각을 유지한 지연 한도,
  고정 환자의 목적지·시작시각·자원 점유 유지, 초기해 재검증, 대체 반환, 버전 검사.
- test_solver_improved.py: 9개 테스트 통과, 작은 사례의 전수열거 최적값 일치.
- experiment_improved.py: 40회 가상 실험, 저장된 실행에서는 24 OPTIMAL / 16 FEASIBLE.
  모두 관측 입력에 유효하고 초기 순차 배정 대비 목적값이 악화되지 않았음.
  정보 오차 3조건의 24회는 숨겨진 실제 상태에서 제약이 깨짐. 이를 해결했다고 주장 금지.
- prototype/index.html + workflow.js: 독립 응답 화면, 원클릭 수용, 불가 사유,
  60초 경고, 120초 재문의, 기관 제한 만료 후 재확인, 1/5/10건 사용성 측정·JSON 저장.
- 화면 상태 테스트와 실제 브라우저 조작 검사 완료. 사람 대상 실험은 미실시.
- 최신 상세 보고서는 응급환자_이송체계_문제점과_개선방안_v2.docx 7쪽.

## 아직 하지 않은 작업
UI와 솔버 통합, 실제 병원 통신, 도로망과 Dijkstra 연결, 원자적 확정 트랜잭션,
백그라운드 알림, 네트워크 장애 처리, 의료진 사용성 검증, 공개 통계로 입력 보정,
운영 중 반복 재계산과 실제 지연 평가. 사용자의 추가 지시에 따라 진행할 것.
시작시각까지 고정하는 현재 재계산은 임상 프로토콜로 확정된 것이 아님.

## 자료 해석
공식 자료의 상황실 처리시간 중앙값 18분을 전체 이송 평균으로 쓰지 않음.
기관 이용률, 요청 응답률, 실제 수용률은 구분함. 2022 이태원 사례는 역사적 사례.
미응답이나 빈 병상 표시를 개별 환자의 수용 확정으로 취급하지 않음.
시간 한도와 우선순위는 실험 가정이며 의료전문가 합의 기준이 아님.
출처 링크는 조사·제안서 문서 안에 포함되어 있음.

## 다른 PC에서 시작할 프롬프트
이 폴더의 HANDOFF.md와 개선판_README.md를 읽고 프로젝트 상태를 파악해줘.
기존 구현과 미구현 범위를 구분해서, 내가 다음에 요청하는 작업부터 이어서 해줘.
''', encoding='utf-8')
(stage / 'requirements-portable.txt').write_text('ortools==9.15.6755\npython-docx\n', encoding='utf-8')
(stage / 'SETUP_WINDOWS.cmd').write_text('''@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "Expand-Archive -LiteralPath 'portable-runtime.zip' -DestinationPath '.' -Force"
if errorlevel 1 goto fail
"_runtime\\python\\python.exe" -c "import sys; sys.path.insert(0,'.solver_deps'); from ortools.sat.python import cp_model; import docx; print('Runtime ready')"
if errorlevel 1 goto fail
echo Setup complete. Run TEST_WINDOWS.cmd next.
pause
exit /b 0
:fail
echo Setup failed. Keep this output for troubleshooting.
pause
exit /b 1
''', encoding='ascii')
(stage / 'TEST_WINDOWS.cmd').write_text('''@echo off
cd /d "%~dp0"
"_runtime\\python\\python.exe" -m unittest test_solver_improved -v
if errorlevel 1 goto fail
"_runtime\\node.exe" prototype\\test_workflow.cjs
if errorlevel 1 goto fail
echo All tests passed.
pause
exit /b 0
:fail
echo Tests failed or runtime missing. Run SETUP_WINDOWS.cmd first.
pause
exit /b 1
''', encoding='ascii')
(stage / 'RUN_EXPERIMENTS_WINDOWS.cmd').write_text('''@echo off
cd /d "%~dp0"
"_runtime\\python\\python.exe" solver_improved.py
if errorlevel 1 goto fail
"_runtime\\python\\python.exe" experiment_improved.py
if errorlevel 1 goto fail
echo Results saved in optimization_results_v2.
pause
exit /b 0
:fail
echo Run failed. Run SETUP_WINDOWS.cmd first.
pause
exit /b 1
''', encoding='ascii')
runtime = Path('C:/Users/PC/.cache/codex-runtimes/codex-primary-runtime/dependencies')
with ZipFile(stage / 'portable-runtime.zip', 'w', ZIP_DEFLATED, compresslevel=3) as z:
    for source, destination in ((runtime/'python', Path('_runtime/python')), (root/'.solver_deps', Path('.solver_deps'))):
        for p in source.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                z.write(p, (destination / p.relative_to(source)).as_posix())
    z.write(runtime/'node/bin/node.exe', '_runtime/node.exe')
with ZipFile(stage / 'portable-runtime.zip') as z:
    assert z.testzip() is None
manifest = []
for p in sorted(stage.rglob('*')):
    if p.is_file() and p.name != 'TRANSFER_MANIFEST.json':
        manifest.append(dict(path=p.relative_to(stage).as_posix(), bytes=p.stat().st_size,
                             sha256=hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()))
(stage / 'TRANSFER_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(stage=str(stage), files=len(manifest)+1, bytes=sum(p['bytes'] for p in manifest)), ensure_ascii=True), flush=True)
