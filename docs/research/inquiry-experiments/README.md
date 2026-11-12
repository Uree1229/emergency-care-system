# 병원 문의 알고리즘 실험 기록

10월 25~29일에 가상 사례에서 병원에 언제, 몇 곳에 문의할지 비교한 연구 기록입니다. 현재 서버 설정과 실험의 채택 여부는 [알고리즘 안내](../../ALGORITHMS.md)를 참고하세요.

## 공동 문의와 한도

- [첫 문의 보존과 우선 문의 공동 제출](EAT_COHORT_BATCHWAVE_EXPERIMENT.md)
- [후속 문의 계획의 동시 문의 한도 보정](EAT_COHORT_WAVECAP_EXPERIMENT.md)
- [여러 관점의 문의 알고리즘 개선](EAT_DIVERSE_EXPERIMENT_20261026.md)

## 회신에 따른 문의 재평가

- [문의 시점과 계산량 개선](EAT_QUEUE_REVIEW_EXPERIMENT_20261027.md)
- [공개 불가 회신의 다중 환자 검증](EAT_DECLINEWAKE_MULTI_EXPERIMENT_20261028.md)
- [선행 문의 회신을 반영한 재평가](EAT_PREDECESSORWAKE_EXPERIMENT_20261028.md)
- [다른 환자의 공개 회신을 반영한 재평가](EAT_FOREIGNREPLYWAKE_EXPERIMENT_20261028.md)

## 조기 문의와 대기시간

- [공개 이동시간으로 조기 문의 후보 선별](EAT_TRAVELWAKE_EXPERIMENT_20261028.md)
- [빈 공개 대기열을 이용한 조기 문의 복원](EAT_TRAVELRECALL_EXPERIMENT_20261028.md)
- [계획된 문의 한 건을 앞당겨 대기 복원](EAT_PLANWAKE_EXPERIMENT_20261028.md)
- [같은 시각의 문의 계획만 앞당기는 후보](EAT_WAVEPLANWAKE_EXPERIMENT_20261028.md)
- [후속 흐름을 보존하며 문의 부담 감소](EAT_CHAINPLANWAKE_EXPERIMENT_20261028.md)
- [문의 슬롯 보충과 재문의 대기 개선](INQUIRY_REFINEMENT_20261029.md)

초기 연구의 [문의 정책 비교](../../EAT_INQUIRY_EXPERIMENT.md)와 [개인 대기 보호](../../EAT_COHORT_TAILWAIT_EXPERIMENT.md)도 함께 볼 수 있습니다. 재현에 필요한 실행 명령과 산출물 경로는 각 기록에 있습니다.
