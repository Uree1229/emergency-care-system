# USB에서 다른 PC로 작업 이어가기

이 폴더 전체를 다른 PC의 짧은 경로(예: C:\Capstone)로 복사한 뒤 작업하세요.
USB 원본은 백업으로 두는 편이 좋습니다. 기존 PC의 작업 폴더는 삭제하지 않았습니다.

## 바로 볼 파일

- 캡스톤_프로젝트_제안서_병원응답보강_20260923.docx: 병원 수용 응답의 병목과 개선책을 반영한 최신 제안서
- assets/응급환자_병원수용_응답병목_개선프로세스.png: 최신 제안서의 흐름 이미지
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

번들 Python의 python-docx로 build_revised_proposal.py와 build_mentor_proposal.py 등을 실행할 수 있습니다.
build_improvements.py는 기존 조사 문서와 optimization_results_v2의 결과를 사용합니다.
문서 레이아웃 검사는 대상 PC의 Word 또는 별도 DOCX 렌더러가 필요합니다.
prototype/check_browser.cjs는 원래 PC의 Playwright와 Edge 절대 경로를 사용하는
개발자용 검사이므로 다른 PC에서는 경로와 의존성을 조정해야 합니다.

## 복사 검증

TRANSFER_MANIFEST.json에 파일별 SHA-256이 있습니다. 자동 전달 과정에서 USB 파일과
비교합니다. 문서와 소스는 일반 폴더로, 실행 환경은 별도 ZIP으로 보관했습니다.
Git 내부 이력, 임시 렌더링 파일, Python 캐시는 제외했습니다. 제안서 PDF와 최종 프로세스 이미지는 포함했습니다.
Codex 계정·로그인·전체 채팅 이력은 포함되지 않으며 HANDOFF.md가 맥락을 요약합니다.
