# 서버 음성 경로 정확도: 문장 단위 판단 수정 전

생성 2026-10-30 13:23:04 · 검증 문장 v3 40개 · 잠금 `voice_server_scope_before_20261030_lock.json`

| 지표 | 서버 | voice_lab v1(참고) |
|---|---:|---:|
| 문장 완전 일치 | 16/40 | 25/40 |
| 칸 재현율 | 117/185 (63.2%) | 174/185 (94.1%) |
| 칸 정밀도 | 97.5% (틀림 3, 남는 칸 0) | 92.6% (틀림 5, 남는 칸 9) |
| 필수 5칸 재현율·정밀도 | 78.9% · 96.6% | 92.7% · 94.4% |
| 활력징후 재현율·정밀도 | 33.3% · 100.0% | 100.0% · 87.7% |
| 나이·발생시각·2차 고려사항 재현율·정밀도 | 63.2% · 100.0% | 84.2% · 100.0% |

## 서버가 틀린 문장

- `w01` 67세 남성 우측 편마비, 언어 이상, 마지막 정상 1시간 전, 의식 명료, 혈압 190에 100, 케이타스 2 — 못 찾음 age; 못 찾음 onset_at; 못 찾음 vitals.sbp; 못 찾음 vitals.dbp
- `w02` 흉통 30분 전 발생, 식은땀, 심근경색 가능성, 혈압 100에 70, 맥박 58, 산소포화도 95, 등급 2 — 못 찾음 onset_at; 못 찾음 vitals.sbp; 못 찾음 vitals.dbp; 못 찾음 vitals.hr; 못 찾음 vitals.spo2
- `w03` 50대 여성 교통사고, 복부 통증 호소, 내부 출혈 의심, 혈압 85에 50, 맥박 125, 의식 명료, 등급 1 — 못 찾음 vitals.sbp; 못 찾음 vitals.dbp; 못 찾음 vitals.hr
- `w04` 호흡 곤란 심함, 청색증 의심, 산소포화도 82, 호흡수 36, 의식 저하, 등급 1 — 못 찾음 vitals.spo2; 못 찾음 vitals.rr; 못 찾음 avpu
- `w05` 8세 여아 땅콩 먹다 기도 폐쇄 추정, 기침 계속, 산소포화도 93, 의식 명료 — 못 찾음 age; 못 찾음 age_group; 못 찾음 chief_complaint; 못 찾음 vitals.spo2
- `w06` 체온 40도 정도, 오한, 의식 명료, 혈압 110에 70 — 못 찾음 vitals.sbp; 못 찾음 vitals.dbp
- `w07` 70대 할아버지 어지러움 호소, 실신 여부 불확실, 맥박 40, 혈압 80에 50, 등급 2 — 못 찾음 vitals.hr; 못 찾음 vitals.sbp; 못 찾음 vitals.dbp
- `w08` 보행 이상, 감각 이상 호소, 45세, 혈압 160에 95, 의식 명료 — 못 찾음 age; 못 찾음 vitals.sbp; 못 찾음 vitals.dbp
- `w10` 60세 이상으로 보이는 남성, 의식 없음, 무호흡, 등급 1 — 못 찾음 age_group
- `w11` 임신 20주, 복통, 질 출혈 소량, 혈압 110에 70, 의식 명료 — 틀림 chief_complaint: obstetric → abdominal
- `w12` 경련 중, 5분 이상 지속, 의식 없음, 체온 38.9, 등급 1 — 못 찾음 vitals.temp
- `w14` 흉통은 없다고 함, 호흡곤란 호소, 쌕쌕거림, 산소포화도 90, 천식 병력 있음, 등급 2 — 못 찾음 chief_complaint; 못 찾음 breathing; 못 찾음 vitals.spo2; 못 찾음 pre_ktas
- `w15` 아버지는 심장병 있었고, 환자 본인은 흉통, 의식 명료, 등급 3 — 못 찾음 chief_complaint; 못 찾음 avpu; 못 찾음 pre_ktas
- `w16` 어머니가 가슴이 아프다고 하심, 의식 명료, 혈압 150에 90 — 못 찾음 chief_complaint; 못 찾음 avpu; 못 찾음 vitals.sbp; 못 찾음 vitals.dbp
- `w19` 출혈량 많음 추정, 다리 개방성 골절, 혈압 90에 60, 맥박 120, 등급 1 — 못 찾음 vitals.sbp; 못 찾음 vitals.dbp; 못 찾음 vitals.hr
- `w20` 65세, 당뇨 환자, 저혈당 의심, 혈당 45, 식은땀, 의식 혼미 — 못 찾음 age; 못 찾음 vitals.glucose; 못 찾음 avpu
- `w22` 숨 쉴 때 가슴 통증, 기침, 열 38도 정도, 산소포화도 94 — 못 찾음 vitals.spo2
- `w27` 갑자기 쓰러진 후 경련, 지금은 멈춤, 의식 저하, 혈압 100에 60 — 못 찾음 seizure_ongoing
- `w30` 생후 6개월 영아 고열, 경련 의심, 체온 39.5, 등급 2 — 틀림 chief_complaint: altered_seizure → other; 못 찾음 vitals.temp
- `w31` 70대 남성, 과거 뇌경색 병력, 이번엔 흉통, 혈압 170에 100, 의식 명료, 등급 2 — 못 찾음 age_group; 못 찾음 chief_complaint; 못 찾음 vitals.sbp; 못 찾음 vitals.dbp; 못 찾음 avpu; 못 찾음 pre_ktas
- `w32` 혈압 150에 90 정도로 보임, 맥박 88, 의식 명료 — 못 찾음 vitals.hr
- `w33` 벌 쏘임 후 호흡곤란 가능성, 입술 부종, 혈압 100에 60, 등급 2 — 틀림 chief_complaint: toxic_env → dyspnea; 못 찾음 vitals.sbp; 못 찾음 vitals.dbp
- `w36` 의식 확인 필요, 호흡 정상, 혈압 120에 80 — 못 찾음 breathing; 못 찾음 vitals.sbp; 못 찾음 vitals.dbp
- `w37` 남자 30대 추정, 칼에 찔림, 복부 출혈, 혈압 80에 40, 의식 저하, 등급 1 — 못 찾음 age_group; 못 찾음 vitals.sbp; 못 찾음 vitals.dbp; 못 찾음 avpu
