# 문서 안내

처음 보는 팀원은 [루트 README](../README.md) → [현재 상태](PROJECT_STATUS.md) → [API 명세](API.md) 순서로 읽으면 됩니다.

| 목적 | 문서 |
|---|---|
| 구현할 기능과 ID | [전체 구현 기능 롱리스트](design/캡스톤_전체구현기능_롱리스트.txt) ([Word](design/캡스톤_전체구현기능_롱리스트.docx)) |
| 데이터 확보 여부와 검증할 항목 | [데이터 확보 가능성 조사](research/데이터확보_조사_20260930.md), [API 필드 매핑 작업표](../api_field_mapping.md) |
| 배정 모델·재계산 절차 | [배정 알고리즘 구체화](design/배정알고리즘_구체화_20260930.md) |
| 화면과 서버를 연결할 때 | [공통 서버 API 명세](API.md), [서버 구현 안내](../server/README.md) |
| 가상 실험의 비교 결과 | [B0/B1 결과](../simulation_results/summary.md), [솔버 결과 설명](../optimization_results/README.md) |
| 문제 배경·제안서 | [최적화 문제 정의](design/최적화_문제정의_v1.md), [현재 제안서](proposals/current/캡스톤_프로젝트_제안서_병원응답보강_20260923.docx), [이전 제안서](proposals/archive/) |
| 원인 조사와 검토 자료 | [시범사업 문제점과 솔버 실험](research/응급의료_시범사업_문제점과_솔버실험.md), `research/`의 Word 조사자료 |
| 제출 산출물 | [멘토링 보고서](reports/캡스톤_멘토링_보고서_작성본.docx), [최종보고서 뼈대](reports/캡스톤_최종보고서_뼈대.docx) |
| USB 백업에서 작업할 때 | [USB 시작 안내](../USB_시작안내.md), [로컬 작업환경](../LOCAL_SETUP.md) |

같은 주제의 예전 제안서는 `proposals/archive/`에 보관했습니다. 날짜가 있는 조사·제안서와 현재 구현 상태는 다를 수 있으므로, 진행 상황은 [현재 상태](PROJECT_STATUS.md)와 코드를 기준으로 확인하세요. 문서 생성용 `build_*.py`는 제안서·보고서 재생성 도구이며 서버 실행에는 필요하지 않습니다.
