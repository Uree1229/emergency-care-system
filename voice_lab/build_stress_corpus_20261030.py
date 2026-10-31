"""Build an independent synthetic voice-input challenge corpus.

This generator uses planned facts and public field contracts only. It does not
import, call, or inspect an extractor, model, STT provider, HTTP API, or server.
Run this file with Python to regenerate the two owned artifacts deterministically.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "voice_lab/corpus/voice_stress_20261030.json"
OUT_TEXT = ROOT / "outputs/음성시험_긴문장_다양성_20261030.txt"
NOW = 2_000_000_000
CORE = ["age", "sex", "symptoms", "denied_symptoms", "uncertain_symptoms",
        "consciousness", "breathing", "systolic_bp", "diastolic_bp", "pulse",
        "spo2", "temperature", "respiratory_rate", "glucose", "onset_at"]
FORBIDDABLE = CORE + ["age_group", "avpu", "chief_complaint", "age_category",
                     "pre_ktas", "stemi_suspected", "injury_region", "labor",
                     "pregnancy_weeks", "seizure_ongoing", "bleeding_risk",
                     "high_risk_mechanism", "pain_score", "gcs"]
NUMERIC = {"age", "systolic_bp", "diastolic_bp", "pulse", "spo2", "temperature",
           "respiratory_rate", "glucose", "onset_at"}

# Each seed gives a distinct setting, account, source distinction, and practical
# detail. The vital signs below are invented planned values, not clinical labels.
SCENARIOS = [
 ("이동중_흉통", ["흉통"], [
  ("시외버스 대합실", "표를 바꾸려고 줄을 서다가 가슴 가운데가 아프다고 직접 말했습니다", "승차권을 찾느라 잠깐 말을 멈췄지만 질문을 못 알아들어서 멈춘 것은 아니라고 합니다", "옆자리 승객은 환자를 오늘 처음 봤고 가족력이나 평소 상태는 모른다고 했습니다"),
  ("아파트 계단참", "우편물을 들고 올라오다 가슴 통증 때문에 난간 옆에 앉았다고 설명했습니다", "몇 층까지 올라왔는지는 기억하지만 층수는 몸 상태 수치와 관계없는 동선 정보입니다", "동행인은 문을 열어 주러 나온 이웃이며 환자 나이나 측정 결과를 대신 말하지 않았습니다"),
  ("미술관 휴게 의자", "전시 설명을 듣던 중 흉통이 생겨 관람을 멈췄다고 진술했습니다", "전시실 안내 방송과 본인 목소리가 겹쳐 같은 문장을 다시 천천히 말해 주었습니다", "안내 직원이 손님 명단을 확인하는 소리는 배경 대화이고 환자의 성별을 확인하는 자료는 아닙니다"),
  ("공원 자판기 앞", "산책을 끝내고 물을 사려던 때 가슴이 아프기 시작했다고 말했습니다", "산책 거리나 걸음 수는 아직 확인하지 않았으며 수치로 바꾸어 기록하지 않습니다", "함께 걷던 친구는 본인의 건강검진 이야기를 했으나 그것은 이번 환자의 검사 결과가 아닙니다"),
  ("세탁소 대기 의자", "옷을 찾으러 와서 기다리다가 가슴 통증을 느꼈다고 합니다", "맡긴 옷이 어느 것인지 설명하던 말과 증상을 설명하던 말을 나누어 전달합니다", "가게 주인은 환자의 가족이 아니고 현장에서 보았던 장면만 알려 주었습니다"),
 ]),
 ("식사전후_복부불편", ["복통", "오심"], [
  ("도서관 구내식당", "식판을 내려놓던 중 배가 아프고 메스껍다고 직접 말했습니다", "어떤 음식을 먹었는지는 아직 서로 진술이 달라 특정 음식이 원인이라고 쓰지 않습니다", "같은 식탁의 동료에게 비슷한 증상이 있는지도 확인되지 않았습니다"),
  ("야간 물류 휴게실", "쉬는 시간에 복통과 오심이 있어 의자에 앉았다고 설명했습니다", "근무표에 적힌 숫자는 근무 순서이며 혈압이나 맥박을 나타내는 값이 아닙니다", "관리자는 교대 시간을 알려 주었고 몸 상태에 대한 진술은 환자가 따로 했습니다"),
  ("농산물 직매장", "장바구니를 정리하면서 배가 아프고 울렁거린다고 이야기했습니다", "구입한 농산물의 무게와 가격은 이번에 읽을 활력징후와 구분합니다", "판매자는 환자가 이전에도 방문했다는 사실만 알고 과거 병력은 모릅니다"),
  ("독립서점 강연장", "강연 중 자리를 나와 복통과 메스꺼움을 호소했습니다", "강연이 끝나는 시간과 증상 시작 시각이 같다고 말한 것은 아니므로 혼동하지 않습니다", "동행자는 증상 표현을 대신 진단명으로 바꾸지 않고 환자 표현 그대로 전달해 달라고 했습니다"),
  ("기차역 물품보관소", "짐을 꺼내다 배가 아프고 오심이 든다고 말했습니다", "보관함 표시 숫자는 환자 나이나 체온으로 읽지 않도록 따로 이야기합니다", "출발을 서두르는 대화가 있었지만 아직 확인하지 않은 관찰을 정상으로 채우지는 않습니다"),
 ]),
 ("호흡관련_직접호소", ["호흡곤란", "기침"], [
  ("실내 체육관 관람석", "경기를 보다가 숨이 차고 기침이 난다고 직접 말했습니다", "방금 운동을 했는지 여부는 확인되지 않았고 관람 중이었다는 장면만 확인했습니다", "코치가 다른 사람에게 불러 준 숫자는 환자 호흡수에 포함하지 않습니다"),
  ("도자기 공방 출입구", "작업을 멈추고 나와 호흡곤란과 기침을 호소했습니다", "실내에 먼지가 있었는지에 대한 서로 다른 의견은 원인 확정으로 기록하지 않습니다", "강사는 수업 재료를 설명했고 환자는 본인 증상을 별도로 설명했습니다"),
  ("지하철 역사 통로", "걸어가던 중 숨이 차고 기침을 해서 벽 쪽에 기대었다고 말했습니다", "전동차 도착 안내 때문에 중간 문장이 끊겼고 끊긴 단어를 임의로 완성하지 않습니다", "역무원이 대합실 위치를 말한 부분은 환자 상태를 말한 부분과 구분합니다"),
  ("공동주택 관리사무소", "서류를 쓰던 중 숨쉬기 힘들고 기침이 나온다고 이야기했습니다", "관리비 내역의 금액은 읽지 않았고 지금 전달하는 값은 측정 항목 이름을 붙여 읽습니다", "관리인은 환자의 평소 호흡 상태는 알지 못한다고 분명히 말했습니다"),
  ("지역 공연장 로비", "공연 시작을 기다리다가 숨이 차고 기침을 한다고 말했습니다", "주변에서 공연 대사를 연습한 소리가 들리지만 환자의 발화와 섞지 않습니다", "같이 온 지인은 좌석을 찾으러 갔다가 돌아왔으며 떨어져 있던 동안의 상태는 모릅니다"),
 ]),
 ("넘어짐_직접외상", ["외상", "출혈"], [
  ("자전거 보관대 옆", "자전거를 세우다 넘어져 다쳤고 손등에서 출혈이 있다고 말했습니다", "자전거 속도나 충돌 정도는 보지 못했으므로 숫자로 추정하지 않습니다", "지나가던 사람은 넘어진 뒤부터 보았다고 했고 그 전 장면은 전달하지 않았습니다"),
  ("화분 판매장 통로", "빈 화분을 옮기다 발을 헛디뎌 다쳤고 팔에 출혈이 보인다고 합니다", "판매장 물품 번호는 상처 크기나 출혈량을 의미하지 않습니다", "점원이 말한 파손 물품 목록과 환자 관찰 기록을 구별해 읽겠습니다"),
  ("학교 운동장 가장자리", "걷다가 미끄러져 다쳤고 무릎에서 피가 난다고 직접 이야기했습니다", "주변 학생들이 외친 점수는 경기 점수이며 통증 점수로 보고하지 않았습니다", "담당 교사는 사고 뒤 환자와 대화한 내용만 알려 주었고 낙상 높이는 모릅니다"),
  ("박물관 외부 계단", "계단을 내려가다 넘어져 다쳤고 손가락 출혈이 있다고 했습니다", "계단 전체 개수와 실제로 넘어진 위치는 확인 중이며 임의 높이로 환산하지 않습니다", "동행자가 찍은 여행 사진은 현재 상처를 관찰한 자료라고 제시하지 않았습니다"),
  ("공유 작업실 출입문", "문턱에 걸려 넘어져 다쳤고 정강이에 출혈이 있다고 말했습니다", "작업 도구는 옆에 놓여 있었으나 도구에 의해 다쳤는지는 말하지 않았습니다", "작업실 운영자는 문이 열린 방향만 확인했고 외상 원인을 단정하지 않았습니다"),
 ]),
 ("일상중_두통어지럼", ["두통", "어지럼"], [
  ("성인 문해 교실", "글을 읽던 중 머리가 아프고 어지럽다고 직접 말했습니다", "읽던 연습문장에 나온 나이와 본인의 나이를 섞지 않도록 다시 확인했습니다", "교사는 수업 참여 여부만 알고 개인 건강 정보는 대신 답하지 않았습니다"),
  ("사진관 대기 공간", "촬영을 기다리다 두통과 어지럼을 느껴 앉았다고 설명했습니다", "촬영 조명에 관한 대화는 있었지만 조명이 원인이라는 판단은 말하지 않았습니다", "사진사는 예약 내용을 알려 주었고 환자 증상은 환자에게 직접 들었습니다"),
  ("동네 우체국 창구 앞", "번호표를 들고 기다리다가 머리가 아프고 어지럽다고 했습니다", "호출 번호를 환자 나이로 옮기지 않도록 숫자에 항목 이름을 붙여 전달합니다", "직원은 환자와 이전에 면식이 없고 오늘 현장에서 본 일만 말했습니다"),
  ("환경 교육관 마당", "설명을 듣던 중 두통과 어지럼 때문에 일행에서 잠시 떨어졌다고 말했습니다", "교육 일정과 몸 상태 변화의 시각을 따로 확인했고 서로 같다고 가정하지 않습니다", "인솔자가 말한 이동 경로와 환자가 말한 증상을 구분해서 정리합니다"),
  ("오래된 음반 가게", "음반을 고르던 중 머리가 아프고 어지러워 의자에 앉았다고 합니다", "가게 음악 소리가 작아진 사실만으로 환자 청력이나 의식 상태를 판단하지 않습니다", "동행자는 계산을 하느라 잠깐 다른 곳에 있었고 그 동안은 관찰하지 못했습니다"),
 ]),
 ("발열기침_관찰", ["발열", "기침"], [
  ("작은 사무실 회의실", "회의 중 열이 나고 기침이 있다고 직접 말했습니다", "회의 참석자 수는 환자 체온이나 호흡수와 관련 없는 상황 설명입니다", "동료가 지난주 아팠다는 이야기는 이번 환자의 과거력으로 옮기지 않습니다"),
  ("지역 합창 연습실", "연습을 쉬면서 발열과 기침을 호소했습니다", "노래를 길게 부른 사실만으로 지금의 호흡 상태를 정상이라고 정하지 않습니다", "반주자는 환자의 음역대를 말했을 뿐 현재 측정 수치를 말한 것은 아닙니다"),
  ("공공 빨래방", "세탁이 끝나기를 기다리면서 열이 나고 기침이 난다고 했습니다", "건조기 표시 온도는 체온계에 나온 값과 다르므로 환자 체온으로 쓰지 않습니다", "옆 이용자는 환자를 모르는 사람이고 현장에서 들은 기침 소리만 알려 주었습니다"),
  ("공방 재료 창고", "재료를 정리하다 발열과 기침 때문에 쉬겠다고 말했습니다", "창고 습도와 환자 산소포화도는 서로 다른 항목이며 주변 환경값은 기록하지 않습니다", "동료는 환자의 지난 근무 기록만 알고 오늘 이전의 몸 상태는 모릅니다"),
  ("지역 영화제 안내소", "안내 업무를 하던 중 열이 나고 기침을 한다고 말했습니다", "관객에게 안내한 상영 시간은 증상 발생 시각으로 사용하지 않습니다", "운영 담당자는 환자와 별도로 무전 중이었고 그 내용은 환자 문진이 아닙니다"),
 ]),
 ("피부증상_환경서술", ["발진", "부종"], [
  ("도심 옥상 정원", "화단을 보다가 팔에 발진과 부종이 보인다고 직접 말했습니다", "어떤 식물에 닿았는지는 기억이 분명하지 않아 노출 원인을 확정하지 않습니다", "관리자가 식물 이름을 나열했지만 그 이름이 환자 진단명은 아닙니다"),
  ("중고 의류 교환 행사장", "옷을 고르던 중 손목에 발진과 부종이 있다고 말했습니다", "천의 재질에 대한 추측은 여러 가지였고 환자는 특정 물질을 확인하지 않았습니다", "행사 진행자는 물품 정보를 알고 있으나 환자 알레르기 병력은 알지 못합니다"),
  ("수공예 비누 전시장", "전시를 둘러본 뒤 손에 발진과 부종을 보았다고 이야기했습니다", "진열품을 만졌는지 확실하지 않다고 하여 접촉 사실을 만들어 넣지 않습니다", "설명서에 적힌 성분표와 현재 환자가 말한 증상을 따로 전달합니다"),
  ("반려식물 교환 모임", "모임 중 팔꿈치 주변 발진과 부종을 발견했다고 말했습니다", "주변 사람들의 경험담이 이어졌으나 환자에게 같은 일이 있었다고 쓰지는 않습니다", "모임 운영자는 관찰 위치만 알려 주었고 증상이 시작된 이유는 모른다고 했습니다"),
  ("목공 체험장 대기석", "체험을 기다리다 손등에 발진과 부종이 있다고 말했습니다", "먼지와 장갑에 대한 대화는 있었으나 어느 쪽과 관련 있는지 확인되지 않았습니다", "강사가 다른 참가자에게 한 안내는 이번 환자에 대한 처치 지시가 아닙니다"),
 ]),
 ("여성환자_직접관찰", ["복통", "출혈"], [
  ("주민센터 상담 대기실", "대기 중 복통과 출혈이 있다고 본인이 말했습니다", "출혈 위치와 양에 대해서는 아직 구체적인 표현이 없어 그 내용을 더하지 않습니다", "함께 온 사람은 일정만 확인해 주었고 임신 여부는 누구도 말하지 않았습니다"),
  ("야간 편의점 휴게 공간", "잠깐 쉬던 중 배가 아프고 출혈이 있다고 직접 설명했습니다", "근무 교대 시간은 증상 시작 시각과 별개이며 원인을 근무 형태로 정하지 않습니다", "동료가 본인 가족 이야기를 한 부분은 환자 개인 정보와 구분합니다"),
  ("지역 강의실 복도", "강의를 듣다 나온 뒤 복통과 출혈을 호소했습니다", "어떤 검사를 받았는지 아직 말하지 않았으므로 검사 결과를 가정하지 않습니다", "동행자는 가족 관계를 설명하지 않았고 환자의 나이는 직접 답한 값을 사용합니다"),
  ("공항 셔틀 정류장", "버스를 기다리다가 복통과 출혈이 있다고 말했습니다", "여행 일정이나 짐 개수로 발생 시각이나 통증 정도를 계산하지 않습니다", "옆 승객이 안내를 도왔지만 환자의 의학적 상태를 대신 확인하지 않았습니다"),
  ("동네 공방 상담석", "체험 상담 중 복통과 출혈을 이야기했습니다", "출혈이 있다는 본인 표현만 기록하고 관찰하지 않은 양상은 붙이지 않습니다", "상담 직원은 환자의 개인적인 병력이나 가족력을 알지 못한다고 했습니다"),
 ]),
 ("소아청소년_보호자구분", ["구토", "설사"], [
  ("어린이 과학관 휴게실", "환아가 배가 불편하다고 말했고 보호자는 구토와 설사가 있었다고 환아에 대해 설명했습니다", "보호자가 잠깐 자리를 비웠던 동안의 횟수는 확인되지 않아 횟수를 적지 않습니다", "보호자 본인의 나이와 환아 나이를 따로 확인했으며 아래 나이는 환아의 나이입니다"),
  ("방과후 교실 현관", "보호자가 환아에게 구토와 설사가 있었다고 말했고 환아도 같은 내용을 고개를 끄덕이며 확인했습니다", "교실 일과표의 시간은 환아의 증상 시작 시각과 별도로 둡니다", "옆 보호자의 자녀에게 있었던 일은 이번 환아 증상으로 포함하지 않습니다"),
  ("어린이 도서관 책장 옆", "환아가 구토와 설사를 했다고 직접 말했고 함께 온 보호자가 들은 내용을 확인했습니다", "읽던 그림책 속 인물의 나이와 환아의 실제 나이는 서로 다른 정보입니다", "사서는 환아가 앉아 있던 위치만 알려 주었고 증상 횟수는 말하지 않았습니다"),
  ("청소년 수련관 안내 데스크", "환자가 구토와 설사가 있다고 본인 말로 설명했습니다", "담당 지도자가 프로그램 시간을 설명했으나 이를 발생 시각으로 옮기지 않습니다", "동행 보호자가 본인의 과거 진료 경험을 말한 대목은 환자 진술과 구분합니다"),
  ("가족 체험 농장 휴게막", "환아에게 구토와 설사가 있었다고 보호자가 말했고 현재 환아에게 다시 확인했습니다", "먹은 음식 목록은 아직 정확하지 않아 특정 음식을 원인으로 적지 않습니다", "농장 직원은 방문 동선만 설명했고 환아의 측정값은 아래에서 따로 읽습니다"),
 ]),
 ("고령환자_정보출처", ["어지럼", "오심"], [
  ("복지관 장기 교실", "환자가 어지럽고 메스껍다고 직접 말했습니다", "옆 사람이 대신 말을 시작했으나 환자가 본인 표현으로 다시 설명한 내용을 사용합니다", "복지관 명부의 오래된 정보는 이번에 직접 확인한 나이와 섞지 않습니다"),
  ("주택 마당 의자", "환자가 정리를 하다 어지럼과 오심을 느껴 앉았다고 설명했습니다", "이웃이 평소 모습과 달라 보인다고 했지만 그것만으로 의식 상태를 정하지 않습니다", "방문한 가족이 본인 혈압을 비교한 말은 환자의 활력징후가 아닙니다"),
  ("지역 역사 강좌 강의실", "환자가 강의를 듣다가 어지럽고 메스껍다고 말했습니다", "수업 중 질문에 답한 사실과 현재 관찰한 의식 상태를 나누어 전달합니다", "강사는 생년을 알지 못해 나이를 추정하지 않았고 환자가 직접 나이를 답했습니다"),
  ("재래시장 쉼터", "환자가 물건을 내려놓고 어지럼과 오심을 이야기했습니다", "시장 방송에 나온 가격 숫자는 환자 수치가 아니므로 항목별로 천천히 읽습니다", "함께 온 지인은 오래 알고 지냈으나 현재 측정값은 알지 못했습니다"),
  ("노인대학 악기 연습실", "환자가 연습을 멈추고 어지럽고 메스껍다고 직접 호소했습니다", "악보의 박자와 맥박은 다른 정보이며 리듬을 환자 수치로 옮기지 않습니다", "교사가 환자의 출석 상태를 설명한 것은 건강 상태를 정상이라고 말한 것이 아닙니다"),
 ]),
 ("작업현장_요통", ["요통"], [
  ("인쇄 작업장 정리 구역", "종이를 정리하던 중 허리가 아프다고 직접 말했습니다", "종이 묶음 무게는 재지 않았으며 무거웠다는 말만으로 수치화하지 않습니다", "작업 반장은 이동 동선만 보았고 통증의 원인에 대해서는 답하지 않았습니다"),
  ("공유 주방 식자재실", "보관함을 열다가 허리 통증을 느꼈다고 설명했습니다", "보관함 높이와 실제 몸을 움직인 정도는 확인되지 않았고 원인도 단정하지 않습니다", "동료는 본인의 예전 요통을 이야기했으나 아래 내용은 현재 환자 진술입니다"),
  ("의상 보관 창고", "옷걸이를 정리하다 허리가 아프기 시작했다고 말했습니다", "옷 상자의 번호는 나이와 관련 없고 재고 수량은 환자 측정값에 포함하지 않습니다", "담당자는 문을 연 시각만 알려 주었으며 증상 시작 시각은 환자에게 물었습니다"),
  ("지역 정원 관리 도구실", "도구를 내려놓던 중 요통을 느꼈다고 직접 말했습니다", "바닥이 젖어 있었는지에 대한 의견이 갈려 넘어진 사실을 덧붙이지 않습니다", "근처 작업자는 환자가 도구를 놓은 모습만 보았다고 구분해서 설명했습니다"),
  ("전시장 철수 작업 구역", "안내판을 정리하다 허리가 아프다고 말했습니다", "철수 일정의 마감 시간은 발생 시각과 같다고 하지 않았으므로 별개로 둡니다", "팀장이 작업 목록을 부른 부분과 환자가 몸 상태를 답한 부분을 따로 읽습니다"),
 ]),
 ("두근거림_잡담혼합", ["심계항진"], [
  ("취미 사진 모임 집결지", "모임을 기다리다가 가슴이 두근거린다고 직접 말했습니다", "카메라 설정 숫자와 맥박 수치를 혼동하지 않도록 항목 이름을 먼저 말합니다", "참가자는 지각 이유를 설명했을 뿐 환자가 긴장했다는 판단을 대신 하지 않았습니다"),
  ("온라인 강좌 촬영 스튜디오", "촬영 대기 중 두근거림이 있다고 설명했습니다", "촬영 시간표와 환자 증상 시작 시각은 각각 확인한 정보로 나눕니다", "제작자가 본인 검진 수치를 비교해 말했으나 이번 환자 값으로 기록하지 않습니다"),
  ("취업 행사 상담 부스", "상담 순서를 기다리다 가슴 두근거림을 느꼈다고 말했습니다", "행사 성격을 이유로 원인을 추정하거나 감정 상태를 의학적 사실처럼 쓰지 않습니다", "안내 요원은 대기 위치만 알려 주었고 환자의 과거 증상은 모른다고 했습니다"),
  ("동네 보드게임 카페", "게임을 쉬는 동안 심계항진이 있다고 직접 이야기했습니다", "게임에 사용한 점수와 차례 번호는 현재 환자 활력징후가 아닙니다", "동행자는 환자가 자리를 옮긴 때만 보았고 몸 상태 수치는 듣지 못했습니다"),
  ("소규모 음악 감상실", "음악을 듣던 중 가슴이 두근거린다고 말했습니다", "곡의 빠르기와 실제 맥박은 별개이며 아래 값은 측정 결과로만 전달합니다", "운영자는 볼륨을 조절한 사실을 말했지만 이것을 증상 원인으로 단정하지 않았습니다"),
 ]),
]

FORMATS = ["현장 인계 대화체", "항목별 구조화 보고", "시간 순서 긴 설명", "잡담 뒤 핵심 재정리", "보호자·목격자 출처 분리"]


def split_parts(text: str) -> list[str]:
    """Partition into nearly balanced, contiguous 500–900 character readings."""
    n = max(1, math.ceil(len(text) / 850))
    if n > 1 and len(text) / n < 500:
        n -= 1
    parts, start = [], 0
    for remaining in range(n, 1, -1):
        target = start + round((len(text) - start) / remaining)
        low, high = start + 500, min(start + 900, len(text) - 500 * (remaining - 1))
        candidates = [m.end() for m in re.finditer(r"[.!?。]\s+", text)
                      if low <= m.end() <= high]
        end = min(candidates, key=lambda p: abs(p - target)) if candidates else target
        parts.append(text[start:end])
        start = end
    parts.append(text[start:])
    assert "".join(parts) == text
    return parts


def planned_values(group: int, variant: int) -> dict:
    idx = group * 5 + variant
    age = (3 + variant * 3) if group == 8 else ((72 + variant * 4) if group == 9 else 26 + idx % 43)
    sex = "female" if group == 7 or idx % 2 else "male"
    return {"age": age, "sex": sex, "symptoms": SCENARIOS[group][1],
            "consciousness": "alert", "breathing": "abnormal" if group == 2 else "normal",
            "systolic_bp": 104 + (idx * 7) % 49, "diastolic_bp": 62 + (idx * 3) % 29,
            "pulse": 68 + (idx * 5) % 45, "spo2": 93 + idx % 7,
            "temperature": round(36.2 + (idx % 7) * .2 + (1.5 if group == 5 else 0), 1),
            "respiratory_rate": 16 + idx % 9, "glucose": 84 + idx * 3 % 77,
            "onset_at": NOW - (15 + idx * 4) * 60}


def make_long() -> list[dict]:
    cases = []
    for gi, (category, symptoms, seeds) in enumerate(SCENARIOS):
        for vi, (place, story, distinction, witness) in enumerate(seeds):
            f = planned_values(gi, vi)
            sexword = "여성" if f["sex"] == "female" else "남성"
            mins = (NOW - f["onset_at"]) // 60
            symptom_text = "과 ".join(symptoms)
            vital = (f"현재 환자의 혈압은 {f['systolic_bp']}에 {f['diastolic_bp']} 밀리미터 수은주, "
                     f"맥박은 분당 {f['pulse']}회, 산소포화도는 {f['spo2']}퍼센트입니다. "
                     f"체온은 섭씨 {f['temperature']:.1f}도, 호흡수는 분당 {f['respiratory_rate']}회, "
                     f"혈당은 {f['glucose']} 밀리그램 퍼 데시리터로 측정했습니다.")
            state = "호흡은 비정상입니다" if f["breathing"] == "abnormal" else "호흡은 정상입니다"
            opening = [
                f"현장 인계 시작하겠습니다. 여기는 {place}입니다. 환자에게 직접 물었고 {f['age']}세 {sexword}라고 답했습니다. {story}. 제가 설명 순서를 조금 바꾸더라도 같은 환자 이야기입니다.",
                f"항목을 나누어 보고합니다. 장소는 {place}, 대상은 {f['age']}세 {sexword} 한 명입니다. 첫째 본인이 설명한 상황입니다. {story}. 둘째 아래 측정값은 모두 이 환자에게서 지금 확인한 값입니다.",
                f"경위를 먼저 길게 설명하겠습니다. {place}에서 만난 환자는 {f['age']}세 {sexword}입니다. {story}. 주변에서 들은 이야기와 직접 확인한 내용을 시간 순서대로 나누어 전달하고, 증상 시작 시각은 뒤에서 별도로 분명하게 말하겠습니다.",
                f"주변 이야기가 좀 섞였는데 환자부터 다시 정리할게요. {place}에 있고 환자는 {f['age']}세 {sexword}입니다. {story}. 장소를 찾는 대화가 오갔지만 지금은 환자 한 명에 대한 기록을 이어서 읽는 중입니다.",
                f"정보 출처를 구분해서 인계합니다. {place}의 환자 본인이 나이를 {f['age']}세라고 답했고 성별은 {sexword}로 확인했습니다. {story}. 주변 사람들의 설명은 보았던 장면에 관한 보조 정보이고 아래 수치는 환자에게서 확인한 결과입니다.",
            ][vi]
            body = (f" {distinction}. {witness}. 현재 확인된 증상은 {symptom_text}입니다. "
                    f"이 증상은 지금을 기준으로 정확히 {mins}분 전부터 시작했다고 환자에 대해 확인했습니다. "
                    f"현재 의식은 명료하고 {state}. {vital} "
                    f"{place}에서 메모를 넘겨 읽는 동안 주변에서 하는 다른 대화는 이 환자의 응답이 아닙니다. "
                    f"확인하지 않은 항목은 따로 덧붙이지 않았고, {symptom_text}의 이유나 진단명, 분류 단계는 보고하지 않았습니다. "
                    f"읽는 사람이 중간에 끊어도 대상이 바뀐 것은 아니며 지금 전달한 관찰과 원문을 함께 검토해 주면 됩니다.")
            text = opening + body
            if vi == 0:
                # Twelve extended accounts. Each paragraph has a separate purpose:
                # location, chronology, attribution, communication, measurement,
                # records, and unresolved details; none replays the main sentence.
                extension = [
                    f"조금 더 자세한 현장 배경을 덧붙이겠습니다. {place}에 도착했을 때 주변 공간을 정리하는 사람과 환자에게 말을 거는 사람이 따로 있었습니다. {story}. 그 장면을 직접 본 사람에게는 자신이 본 범위만 말해 달라고 했고, 보지 못한 장면을 예상해서 연결하지 않도록 했습니다. 물건이 놓여 있던 위치와 환자의 이동 방향은 동선 설명에만 사용하며 신체 상태를 나타내는 값으로 바꾸지 않습니다. 환자에 관한 설명이 잠시 멈췄다가 이어져도 새로운 환자를 추가하는 이야기는 아닙니다.",
                    f"발생 경위를 들을 때에는 {distinction}. 환자는 장소를 설명하면서 여러 번 주변을 가리켰고, 질문의 의미를 이해했는지와 증상을 어떻게 표현했는지를 나누어 확인했습니다. 증상 이유에 관한 다른 사람의 의견은 확인된 사실과 구분했습니다. 이미 말한 발생 간격은 증상이 시작된 때에 대한 답이고, 우리가 문을 열고 들어온 때나 물건을 옮긴 때를 대신 넣은 것이 아닙니다. 시간표나 안내문에 적힌 숫자가 들려도 환자에게서 확인한 발생 정보와 섞지 않습니다.",
                    f"목격자 쪽 설명은 이렇습니다. {witness}. 다른 사람이 환자의 말을 반복해서 전했을 때에는 새 증상이 추가된 것인지 같은 말을 다시 한 것인지 확인하고 같은 관찰을 별개 사건처럼 세지 않았습니다. {place}를 관리하는 사람이 주변 일정과 공간 사용 방법을 설명한 부분은 현장을 이해하기 위한 정보입니다. 그 설명으로 환자의 가족 관계, 정확한 나이, 과거 진료 내용이나 현재 몸 상태를 대신 결정하지 않았습니다. 환자가 직접 말하지 않은 병명은 이 인계에 포함하지 않습니다.",
                    f"대화가 이어진 과정을 설명하면, {symptom_text}을 말한 뒤 환자가 가지고 온 소지품을 어디에 둘지 물어보았고 잠시 다른 주제로 대화했습니다. 소지품에 대한 질문이 나왔다고 해서 앞의 증상이 없어졌다는 뜻은 아닙니다. 주변 소음으로 문장 끝이 잘 들리지 않은 부분은 들린 내용만 남겼고, 듣지 못한 단어를 매끄럽게 만들기 위해 정상 소견을 보태지 않았습니다. 같은 항목을 확인하려고 질문을 다시 읽은 소리도 환자 자신의 새 답변과 구분합니다. 마지막에는 대상이 계속 같은 사람임을 확인하고 다음 항목으로 넘어갔습니다.",
                    f"측정 기록을 전달한 순서와 화면에 표시하는 순서가 다를 수 있어 항목 이름을 먼저 읽었습니다. {place}에서 확보한 각 값은 방금 보고한 현재 환자의 측정값이며, 주변 사람의 수치나 기기 사용 설명서의 예시가 아닙니다. 숫자를 천천히 읽으려고 사이에 쉼을 넣더라도 서로 다른 항목의 앞자리와 뒷자리를 붙이지 않습니다. 특정 값이 어떤 의미인지 평가하는 말은 하지 않았고 측정하지 않은 검사를 했다고 표현하지도 않았습니다. 기기 이름을 몰라도 이미 항목 이름을 붙여 읽은 수치의 출처는 환자에게서 얻은 현재 관찰입니다.",
                    f"기록을 정리하면서 아직 비어 있는 부분도 따로 두었습니다. {symptom_text} 외에 말하지 않은 증상은 없다고 단정하지 않았고 가족력이나 복용 중인 약은 질문에 답을 듣지 못한 상태입니다. 주변 사람과 이야기한 대기 시간, 물건의 개수, 이동 방향이 환자의 검사 결과처럼 들어가지 않도록 메모의 출처를 구분했습니다. 앞으로 새로 확인한 값이 생기면 그때의 관찰로 별도 전달하면 됩니다. 지금 이 긴 설명에서는 이미 확인한 원문을 검토하기 위한 정보만 전달하며 원인을 정하거나 어느 기관으로 가야 한다는 지시는 덧붙이지 않습니다.",
                ]
                text += " " + " ".join(extension)
            case = {"id": f"L{gi*5+vi+1:03d}", "category": category,
                    "presentation": FORMATS[vi], "extended": vi == 0,
                    "text": text, "parts": split_parts(text), "expected_fields": f,
                    "forbidden_fields": [],
                    "notes": ["독립 기획값; 부분집합 기대이며 진단·등급·처치 기대 없음",
                              "말하지 않은 증상의 부정이나 가족력으로 환자 값을 생성하면 안 됨",
                              "parts를 연속으로 읽되 전체 본문 한 건의 경계를 유지",
                              "focus: 수치 단위, 주체 구분, 발생 시각, 긴 발화 뒤 누락 여부"]}
            cases.append(case)
    return cases


def make_boundaries() -> list[dict]:
    out = []
    def add(cat, text, exp=None, forbid=None, route="dictation", q=None, note=None):
        row = {"id": f"B{len(out)+1:03d}", "category": cat, "text": text,
               "expected_fields": exp or {}, "forbidden_fields": forbid or [], "route": route}
        if q:
            row["question_id"] = q
        if note:
            row["notes"] = [note]
        out.append(row)

    # 1. Arabic numerals, spacing, zeros, and label boundaries.
    numeric = [
        ("환자는 0세 남아입니다.", {"age":0,"sex":"male"}),
        ("환자 1세 여아입니다.", {"age":1,"sex":"female"}),
        ("환자 9세 남성, 맥박 109회입니다.", {"age":9,"sex":"male","pulse":109}),
        ("환자 10세 여성입니다.", {"age":10,"sex":"female"}),
        ("환자 19세 남성이고 혈당 109입니다.", {"age":19,"sex":"male","glucose":109}),
        ("환자 100세 여성입니다.", {"age":100,"sex":"female"}),
        ("환자 101세 남성입니다.", {"age":101,"sex":"male"}),
        ("환자 109세 여성입니다.", {"age":109,"sex":"female"}),
        ("혈압 101에 61입니다.", {"systolic_bp":101,"diastolic_bp":61}),
        ("혈압 120/80입니다.", {"systolic_bp":120,"diastolic_bp":80}),
        ("맥박 80, 호흡수 18입니다.", {"pulse":80,"respiratory_rate":18}),
        ("산소포화도 99입니다.", {"spo2":99}),
        ("체온 37.0도입니다.", {"temperature":37.0}),
        ("체온 37.1도입니다.", {"temperature":37.1}),
        ("혈당 100입니다.", {"glucose":100}),
        ("혈당 101입니다.", {"glucose":101}),
        ("혈당 109입니다.", {"glucose":109}),
        ("수축기 혈압 130, 이완기 혈압 75입니다.", {"systolic_bp":130,"diastolic_bp":75}),
        ("환자 28세, 체온 38.2도, 맥박 102입니다.", {"age":28,"temperature":38.2,"pulse":102}),
        ("호흡수 0회로 측정했고 호흡 없음입니다.", {"respiratory_rate":0,"breathing":"absent"}),
    ]
    for t,e in numeric: add("숫자_자리경계", t,e)

    korean = [
        ("환자는 스물한 살 남성입니다.", {"age":21,"sex":"male"}),
        ("환자는 서른두 살 여성입니다.", {"age":32,"sex":"female"}),
        ("환자는 마흔다섯 살입니다.", {"age":45}),
        ("환자는 쉰여섯 살입니다.", {"age":56}),
        ("환자는 예순일곱 살입니다.", {"age":67}),
        ("환자는 일흔여덟 살입니다.", {"age":78}),
        ("환자는 여든아홉 살입니다.", {"age":89}),
        ("환자는 아흔 살입니다.", {"age":90}),
        ("혈압 백이십에 팔십입니다.", {"systolic_bp":120,"diastolic_bp":80}),
        ("혈압 백삼십오에 팔십오입니다.", {"systolic_bp":135,"diastolic_bp":85}),
        ("맥박 아흔두 회입니다.", {"pulse":92}),
        ("맥박 백여덟 회입니다.", {"pulse":108}),
        ("호흡수 스물네 회입니다.", {"respiratory_rate":24}),
        ("호흡수 열여덟 회입니다.", {"respiratory_rate":18}),
        ("산소포화도 구십칠 퍼센트입니다.", {"spo2":97}),
        ("산소포화도 백 퍼센트입니다.", {"spo2":100}),
        ("체온 삼십칠 점 이 도입니다.", {"temperature":37.2}),
        ("체온 삼십팔 점 영 도입니다.", {"temperature":38.0}),
        ("혈당 백오십육입니다.", {"glucose":156}),
        ("복통은 삼십 분 전 시작했습니다.", {"symptoms":["복통"],"onset_at":NOW-1800}),
    ]
    for t,e in korean: add("한글수_고유어혼합",t,e)

    units = [
        ("혈압은 126/74 mmHg입니다.", {"systolic_bp":126,"diastolic_bp":74}),
        ("수축기 118 mmHg 이완기 76 mmHg입니다.", {"systolic_bp":118,"diastolic_bp":76}),
        ("맥박은 84회/분입니다.", {"pulse":84}),
        ("심박수는 분당 93회입니다.", {"pulse":93}),
        ("HR 102 bpm입니다.", {"pulse":102}),
        ("PR 79입니다.", {"pulse":79}),
        ("호흡수 22회/분입니다.", {"respiratory_rate":22}),
        ("RR 19입니다.", {"respiratory_rate":19}),
        ("SpO2 96%입니다.", {"spo2":96}),
        ("에스피오투 구십팔 퍼센트입니다.", {"spo2":98}),
        ("체온은 섭씨 36.8도입니다.", {"temperature":36.8}),
        ("BT 37.4입니다.", {"temperature":37.4}),
        ("비티 삼십칠 점 육 도입니다.", {"temperature":37.6}),
        ("혈당 125 mg/dL입니다.", {"glucose":125}),
        ("BST 143입니다.", {"glucose":143}),
        ("비에스티 백육십이입니다.", {"glucose":162}),
        ("두통은 1시간 20분 전에 시작했습니다.", {"symptoms":["두통"],"onset_at":NOW-4800}),
        ("구토는 90분 전에 시작했습니다.", {"symptoms":["구토"],"onset_at":NOW-5400}),
        ("흉통 발생은 2시간 전입니다.", {"symptoms":["흉통"],"onset_at":NOW-7200}),
        ("기침 시작은 3일 전입니다.", {"symptoms":["기침"],"onset_at":NOW-259200}),
    ]
    for t,e in units: add("단위_영문약어",t,e)

    particles = [
        ("맥박이요, 86회예요.", {"pulse":86}),
        ("맥박은요 91로 나왔어요.", {"pulse":91}),
        ("혈압이 132에 84거든요.", {"systolic_bp":132,"diastolic_bp":84}),
        ("혈압은 121에 77이고요.", {"systolic_bp":121,"diastolic_bp":77}),
        ("산소포화도는 97로 확인했어요.", {"spo2":97}),
        ("체온이 37.3도였고 지금 잰 값이에요.", {"temperature":37.3}),
        ("호흡수가 21회라는 말입니다.", {"respiratory_rate":21}),
        ("혈당은 117로 나왔습니다만 원인 이야기는 아니에요.", {"glucose":117}),
        ("환자 나이는 44세고요.", {"age":44}),
        ("환자 성별은 여성이라고 본인이 말했어요.", {"sex":"female"}),
        ("지금 의식이 명료하거든요.", {"consciousness":"alert"}),
        ("호흡은 정상으로 확인했고요.", {"breathing":"normal"}),
        ("흉통이 있다고 합니다.", {"symptoms":["흉통"]}),
        ("복통은 없다고 답했거든요.", {"denied_symptoms":["복통"]}),
        ("두통도 있다고 덧붙였습니다.", {"symptoms":["두통"]}),
        ("어지럼만 있다고 말씀했어요.", {"symptoms":["어지럼"]}),
        ("기침이랑 오심이 있대요.", {"symptoms":["기침","오심"]}),
        ("발진과 부종이 보인다고 하네요.", {"symptoms":["발진","부종"]}),
        ("허리가 아프다는 건 환자 본인 말이에요.", {"symptoms":["요통"]}),
        ("증상 시작은 17분 전이라고요.", {"onset_at":NOW-1020}),
    ]
    for t,e in particles: add("조사_말끝_구어체",t,e)

    negatives = [
        ("흉통 없음입니다.", {"denied_symptoms":["흉통"]}),
        ("복통은 없어요.", {"denied_symptoms":["복통"]}),
        ("호흡곤란은 없습니다.", {"denied_symptoms":["호흡곤란"]}),
        ("두통은 부인합니다.", {"denied_symptoms":["두통"]}),
        ("구토는 안 했습니다.", {"denied_symptoms":["구토"]}),
        ("기침 안 합니다.", {"denied_symptoms":["기침"]}),
        ("출혈은 보이지 않습니다.", {"denied_symptoms":["출혈"]}),
        ("발진 없고 부종도 없습니다.", {"denied_symptoms":["발진","부종"]}),
        ("오심 없지만 복통은 있습니다.", {"denied_symptoms":["오심"],"symptoms":["복통"]}),
        ("흉통 없고 호흡곤란은 있습니다.", {"denied_symptoms":["흉통"],"symptoms":["호흡곤란"]}),
        ("호흡곤란 없고 기침은 합니다.", {"denied_symptoms":["호흡곤란"],"symptoms":["기침"]}),
        ("구토는 없으며 오심만 있습니다.", {"denied_symptoms":["구토"],"symptoms":["오심"]}),
        ("의식은 명료하지 않고 의식 저하입니다.", {"consciousness":"impaired","symptoms":["의식저하"]}),
        ("호흡 정상은 아니고 호흡 비정상입니다.", {"breathing":"abnormal"}),
        ("반응 없지는 않습니다. 현재 의식 명료합니다.", {"consciousness":"alert"}),
        ("무호흡은 아니며 현재 호흡 정상입니다.", {"breathing":"normal"}),
        ("흉통이 없는 것은 아닙니다. 흉통 있습니다.", {"symptoms":["흉통"]}),
        ("두통 없다는 말 취소하고 두통 있다고 정정합니다.", {"symptoms":["두통"]}),
        ("설사는 없고 요통이 있습니다.", {"denied_symptoms":["설사"],"symptoms":["요통"]}),
        ("심계항진은 없다고 했습니다.", {"denied_symptoms":["심계항진"]}),
    ]
    for t,e in negatives: add("부정_대조_이중부정",t,e)

    uncertain = [
        ("흉통 여부 미확인입니다.", {"uncertain_symptoms":["흉통"]},["symptoms"]),
        ("복통이 있는지 아직 모릅니다.", {"uncertain_symptoms":["복통"]},["symptoms"]),
        ("호흡곤란인지 확인 불가입니다.", {"uncertain_symptoms":["호흡곤란"]},["symptoms"]),
        ("구토 여부는 확인 안 됐습니다.", {"uncertain_symptoms":["구토"]},["symptoms"]),
        ("발진이 의심되지만 확인 전입니다.", {"uncertain_symptoms":["발진"]},["symptoms"]),
        ("출혈 가능성만 들었고 아직 확인 못 했습니다.", {"uncertain_symptoms":["출혈"]},["symptoms"]),
        ("의식 상태 미확인입니다.", {"consciousness":"unknown"},[]),
        ("호흡 상태는 확인 불가입니다.", {"breathing":"unknown"},[]),
        ("나이는 모르겠습니다.", {},["age"]),
        ("환자 성별 확인 안 됐습니다.", {"sex":"unknown"},[]),
        ("혈압 미측정입니다.", {},["systolic_bp","diastolic_bp"]),
        ("맥박 아직 안 쟀습니다.", {},["pulse"]),
        ("체온 측정 전입니다.", {},["temperature"]),
        ("산소포화도 미측정입니다.", {},["spo2"]),
        ("호흡수는 아직 세지 않았습니다.", {},["respiratory_rate"]),
        ("혈당은 측정하지 못했습니다.", {},["glucose"]),
        ("발생 시각은 모릅니다.", {},["onset_at"]),
        ("나이는 40세 또는 50세라고 들었고 아직 확인하지 못했습니다.", {},["age"]),
        ("맥박은 80인지 90인지 모르겠습니다.", {},["pulse"]),
        ("혈압 120에 80인지 130에 90인지 확인 중입니다.", {},["systolic_bp","diastolic_bp"]),
    ]
    for t,e,b in uncertain: add("미확인_미측정_대안",t,e,b)

    history = [
        ("환자가 아니라 보호자가 67세 남성입니다.",{},["age","sex"]),
        ("환자 어머니는 72세 여성이고 환자 나이는 모릅니다.",{},["age","sex"]),
        ("보호자 본인 혈압은 140에 90이라고 했습니다. 환자 혈압은 미측정입니다.",{},["systolic_bp","diastolic_bp"]),
        ("아버지가 두통이 있다고 합니다. 환자의 증상은 아직 듣지 못했습니다.",{},["symptoms"]),
        ("동행자가 숨이 차다고 했고 환자에게는 아직 물어보지 않았습니다.",{},["symptoms","breathing"]),
        ("환자는 42세 여성입니다. 남편은 51세 남성입니다.",{"age":42,"sex":"female"},[]),
        ("환자는 13세 남아이고 보호자는 39세 여성입니다.",{"age":13,"sex":"male"},[]),
        ("환자 맥박은 87입니다. 동료 본인 맥박은 105였다고 비교했습니다.",{"pulse":87},[]),
        ("작년 환자 체온은 39.0도였고 오늘 체온은 미측정입니다.",{},["temperature"]),
        ("지난달 환자 혈당은 210이었고 현재 혈당은 아직 모릅니다.",{},["glucose"]),
        ("어제 환자 혈압은 160에 100이었고 현재 혈압은 미측정입니다.",{},["systolic_bp","diastolic_bp"]),
        ("과거에 환자에게 경련이 있었고 이번 현재 증상은 아직 확인 전입니다.",{},["symptoms"]),
        ("가족력으로 실신이 있었다고 했지만 환자 본인 이야기는 아닙니다.",{},["symptoms"]),
        ("친구는 오심이 있고 환자는 복통이 있습니다.",{"symptoms":["복통"]},[]),
        ("가족은 발진이 있었지만 환자는 기침이 있습니다.",{"symptoms":["기침"]},[]),
        ("보호자는 체온 38.5도, 환자 체온은 36.7도입니다.",{"temperature":36.7},[]),
        ("환자는 지금 의식 명료합니다. 보호자는 어제 의식 저하가 있었다고 본인 이야기를 했습니다.",{"consciousness":"alert"},["symptoms","denied_symptoms","uncertain_symptoms"]),
        ("환자 호흡은 정상입니다. 옆 사람은 본인이 호흡곤란이라고 말했습니다.",{"breathing":"normal"},["symptoms"]),
        ("환자 나이는 64세입니다. 설명 책자에 쓰인 예시는 80세 남성입니다.",{"age":64},["sex"]),
        ("기기 설명서의 예시 혈압은 120에 80입니다. 환자에게서는 아직 측정 전입니다.",{},["systolic_bp","diastolic_bp"]),
    ]
    for t,e,b in history: add("과거_가족_다른주체",t,e,b)

    questions = [
        ("혈압이 120에 80인가요?",["systolic_bp","diastolic_bp"]),
        ("맥박은 90이 맞나요?",["pulse"]),
        ("체온 38도인지 물어보세요.",["temperature"]),
        ("산소포화도가 95인지 확인할까요?",["spo2"]),
        ("혈당 150이라고 말한 건가요?",["glucose"]),
        ("호흡수가 20회인가요?",["respiratory_rate"]),
        ("환자 40세 여성인가요?",["age","sex"]),
        ("환자는 남성인가요?",["sex"]),
        ("흉통이 있는지 물어봐 주세요.",["symptoms"]),
        ("호흡곤란 없다는 뜻인가요?",["symptoms","denied_symptoms"]),
        ("의식이 명료합니까?",["consciousness"]),
        ("호흡 정상인지 확인했나요?",["breathing"]),
        ("반응 없다는 말이 맞습니까?",["consciousness"]),
        ("복통은 30분 전 시작한 건가요?",["symptoms","onset_at"]),
        ("두통 없어요?",["denied_symptoms"]),
        ("혈압을 110에 70으로 바꾸라는 요청인가요?",["systolic_bp","diastolic_bp"]),
        ("체온은 37.2로 저장되어 있나요?",["temperature"]),
        ("호흡수 18과 맥박 88 중 어떤 값을 말했나요?",["respiratory_rate","pulse"]),
        ("예를 들어 50세라고 답하면 되나요?",["age"]),
        ("혈당 100이라고 적힌 예시를 읽어 볼까요?",["glucose"]),
    ]
    for t,b in questions: add("질문_예시_인용",t,{},b)

    duplicates = [
        ("맥박 88입니다. 다시 말하면 맥박 88입니다.",{"pulse":88},[]),
        ("흉통 있습니다. 같은 흉통 얘기를 한 번 더 했습니다.",{"symptoms":["흉통"]},[]),
        ("혈압 122에 78, 같은 측정값 122에 78입니다.",{"systolic_bp":122,"diastolic_bp":78},[]),
        ("환자 46세입니다. 나이 46세 다시 확인했습니다.",{"age":46},[]),
        ("체온 37.4도라고 두 번 읽었습니다. 체온 37.4도입니다.",{"temperature":37.4},[]),
        ("산소포화도 97과 93 두 값이 있는데 어느 것이 맞는지 모릅니다.",{},["spo2"]),
        ("맥박 82 또는 92입니다. 현재 값은 확인 중입니다.",{},["pulse"]),
        ("체온 36.8도와 38.6도 중 어느 것인지 아직 미확인입니다.",{},["temperature"]),
        ("나이는 27세와 72세로 서로 다르게 들렸습니다. 확정 전입니다.",{},["age"]),
        ("성별은 남성인지 여성인지 의견이 달라 확인 중입니다.",{"sex":"unknown"},[]),
        ("혈압을 120에 80이라고 읽었지만 잘못 읽었습니다. 정확히 112에 72로 정정합니다.",{"systolic_bp":112,"diastolic_bp":72},[]),
        ("맥박 99가 아니라 89입니다.",{"pulse":89},[]),
        ("체온 38.1은 오기이고 37.1로 수정합니다.",{"temperature":37.1},[]),
        ("산소포화도 91은 잘못 말했고 96이 맞습니다.",{"spo2":96},[]),
        ("나이 61세가 아니고 16세입니다.",{"age":16},[]),
        ("혈당 180을 108로 정정합니다.",{"glucose":108},[]),
        ("호흡수 32회는 오기입니다. 23회로 고칩니다.",{"respiratory_rate":23},[]),
        ("구토 없음이 아니라 구토 있음으로 정정합니다.",{"symptoms":["구토"]},[]),
        ("흉통 있음은 잘못 들은 내용입니다. 흉통 없다고 답했습니다.",{"denied_symptoms":["흉통"]},[]),
        ("발생은 20분 전이 아니라 40분 전으로 정정합니다.",{"onset_at":NOW-2400},[]),
    ]
    for t,e,b in duplicates: add("중복_충돌_명시정정",t,e,b)

    changes = [
        ("맥박이 지금 102로 올랐습니다.",{"pulse":102},[]),
        ("혈압이 지금 108에 68로 바뀌었습니다.",{"systolic_bp":108,"diastolic_bp":68},[]),
        ("현재 산소포화도는 다시 재서 98입니다.",{"spo2":98},[]),
        ("지금 체온은 37.9도로 나왔습니다.",{"temperature":37.9},[]),
        ("지금 호흡수 26회로 새로 확인했습니다.",{"respiratory_rate":26},[]),
        ("현재 혈당 다시 재니 133입니다.",{"glucose":133},[]),
        ("지금은 의식 명료합니다.",{"consciousness":"alert"},[]),
        ("지금은 반응 없습니다.",{"consciousness":"unresponsive"},[]),
        ("현재 의식 저하입니다.",{"consciousness":"impaired","symptoms":["의식저하"]},[]),
        ("지금 호흡 비정상입니다.",{"breathing":"abnormal"},[]),
        ("현재 무호흡입니다.",{"breathing":"absent"},[]),
        ("지금 호흡 정상으로 관찰했습니다.",{"breathing":"normal"},[]),
        ("맥박이 아까보다 떨어졌지만 숫자는 아직 확인하지 않았습니다.",{},["pulse"]),
        ("혈압이 내려갔는데 새 측정값은 아직 모릅니다.",{},["systolic_bp","diastolic_bp"]),
        ("산소포화도가 바뀐 것 같지만 현재 숫자는 확인 전입니다.",{},["spo2"]),
        ("열이 더 나는 것 같지만 체온은 미측정입니다.",{"uncertain_symptoms":["발열"]},["temperature"]),
        ("새로 두통이 있다고 합니다.",{"symptoms":["두통"]},[]),
        ("지금 새로 오심이 생겼습니다.",{"symptoms":["오심"]},[]),
        ("현재는 복통 없다고 답했습니다.",{"denied_symptoms":["복통"]},[]),
        ("새 관찰입니다. 발진과 부종이 있습니다.",{"symptoms":["발진","부종"]},[]),
    ]
    for t,e,b in changes: add("현재변화_숫자없는변화",t,e,b)

    bad = [
        ("고급 정장이라고 인식됐습니다. 원래 단어는 확인하지 못했습니다.",{},["breathing"]),
        ("혈압 백이 십에 팔 씹이라고만 나왔고 정확한 수치는 모릅니다.",{},["systolic_bp","diastolic_bp"]),
        ("맥박 팔 공 또는 발공이라고 나왔습니다. 다시 확인해야 합니다.",{},["pulse"]),
        ("체온 삼십 칠점 이인지 삼십 이점 칠인지 모르겠습니다.",{},["temperature"]),
        ("산소포화도 아흔 뒤 숫자가 끊겨서 아직 미확인입니다.",{},["spo2"]),
        ("호흡수 스물 뒤가 잘려서 정확한 값은 모릅니다.",{},["respiratory_rate"]),
        ("혈당 백 뒤가 끊겼습니다. 측정값을 확정할 수 없습니다.",{},["glucose"]),
        ("환자 나이 서른인지 쉰인지 소리가 뭉개졌습니다.",{},["age"]),
        ("흉 톤이라는 인식 결과만 있고 실제 증상은 미확인입니다.",{},["symptoms"]),
        ("복 탕이라고 나와 원문을 다시 물어봐야 합니다.",{},["symptoms"]),
        ("의식 면료라고 적혔지만 실제로 어떤 답이었는지 모릅니다.",{},["consciousness"]),
        ("정상이라는 단어 앞 항목명이 사라져 무엇이 정상인지 모릅니다.",{},["consciousness","breathing"]),
        ("팔십인지 백팔십인지 맥박 앞부분을 못 들었습니다.",{},["pulse"]),
        ("혈압 12에 8이라고 인식됐지만 원래 숫자는 확인 전입니다.",{},["systolic_bp","diastolic_bp"]),
        ("체온 3.72라고 인식됐고 정확한 소수 위치는 미확인입니다.",{},["temperature"]),
        ("산소포화도 구 칠이라고만 들려 정확한 값은 모르겠습니다.",{},["spo2"]),
        ("발생 시간에서 전이라는 말만 남아 몇 분인지는 모릅니다.",{},["onset_at"]),
        ("기침 앞의 부정어가 잘렸는지 확인할 수 없습니다.",{},["symptoms","denied_symptoms"]),
        ("무호흡인지 유호흡인지 첫 음절을 못 들었고 호흡 상태는 미확인입니다.",{"breathing":"unknown"},[]),
        ("이 기록은 빈 소리였습니다. 인식된 환자 정보가 없습니다.",{},FORBIDDABLE),
    ]
    for t,e,b in bad: add("깨진STT_추측복원금지",t,e,b)

    answers = [
        ("age","스물네 살이요.",{"age":24},[]),
        ("age","서른일곱입니다. 여성이고 맥박은 91이에요.",{"age":37},["sex","pulse"]),
        ("sex","남성이요.",{"sex":"male"},[]),
        ("sex","여성입니다. 나이는 58세입니다.",{"sex":"female"},["age"]),
        ("consciousness","의식 명료해요.",{"consciousness":"alert"},[]),
        ("consciousness","현재 반응 없습니다.",{"consciousness":"unresponsive"},[]),
        ("breathing","호흡 정상입니다.",{"breathing":"normal"},[]),
        ("breathing","확인하지 못했습니다.",{"breathing":"unknown"},[]),
        ("blood_pressure","백이십육에 팔십입니다.",{"systolic_bp":126,"diastolic_bp":80},[]),
        ("blood_pressure","118에 72, 맥박은 86입니다.",{"systolic_bp":118,"diastolic_bp":72},["pulse"]),
        ("pulse","분당 아흔네 회요.",{"pulse":94},[]),
        ("pulse","88이고 산소포화도는 97입니다.",{"pulse":88},["spo2"]),
        ("spo2","구십육 퍼센트요.",{"spo2":96},[]),
        ("temperature","삼십칠 점 오 도입니다.",{"temperature":37.5},[]),
        ("respiratory_rate","분당 스물두 회입니다.",{"respiratory_rate":22},[]),
        ("glucose","백이십삼입니다.",{"glucose":123},[]),
        ("onset_at","마흔 분 전입니다.",{"onset_at":NOW-2400},[]),
        ("symptoms","흉통은 없고 복통이 있습니다.",{"denied_symptoms":["흉통"],"symptoms":["복통"]},[]),
        ("temperature","아직 안 쟀어요.",{},["temperature"]),
        ("blood_pressure","위에 것만 120이고 아래 것은 모릅니다.",{"systolic_bp":120},["diastolic_bp"]),
    ]
    for q,t,e,b in answers: add("후속질문_문맥한정",t,e,b,"followup",q,
                               "question_id에 해당하는 답만 반영; 다른 항목은 ignored_fields 대상")
    for row in out:
        if row["id"] == "B098" or "B171" <= row["id"] <= "B180":
            row.setdefault("notes", []).append(
                "신규 의미 스트레스 계약: dictation 내 명시 정정의 최종 진술을 기대함. "
                "기존 speech_eval/ANNOTATION.md의 정정 dictation 제외 정책과 별도 평가하며 "
                "기존 벤치마크 점수나 지원 보증에 합산하지 않음")
    return out


def make_flows() -> list[dict]:
    flows = []
    for i in range(24):
        gi, vi = i % 12, (i // 12) + 1
        cat, symptoms, seeds = SCENARIOS[gi]
        place, story, _, _ = seeds[vi]
        f = planned_values(gi, vi)
        gender = "여성" if f["sex"] == "female" else "남성"
        mins = (NOW-f["onset_at"])//60
        initial = {k:f[k] for k in ("age","sex","symptoms","onset_at")}
        steps = [
            {"mode":"dictation", "text":f"{place}에서 만난 {f['age']}세 {gender} 환자입니다. {story}. 현재 증상은 {'과 '.join(symptoms)}이고 발생은 {mins}분 전입니다.",
             "expected_fields":initial,"forbidden_fields":[], "notes":["새 세션; 최초 임상정보"]},
            {"mode":"answer","question_id":"blood_pressure","text":f"{f['systolic_bp']}에 {f['diastolic_bp']}입니다. 맥박은 {f['pulse']}라고도 말했어요.",
             "expected_fields":{"systolic_bp":f['systolic_bp'],"diastolic_bp":f['diastolic_bp']},"forbidden_fields":["pulse"],
             "notes":["혈압 질문 답변에서 맥박이 별도 저장되면 안 됨"]},
            {"mode":"answer","question_id":"pulse","text":f"분당 {f['pulse']}회입니다.",
             "expected_fields":{"pulse":f['pulse']},"forbidden_fields":[]},
            {"mode":"auto","text":f"지금 다시 잰 맥박은 {f['pulse']+7}로 바뀌었습니다.",
             "expected_fields":{"pulse":f['pulse']+7},"forbidden_fields":[],"intent":"observe",
             "notes":["이전값 정정이 아닌 새 측정 관찰"]},
            {"mode":"command","text":"지금 입력한 환자 정보 읽어줘.","expected_fields":{},"forbidden_fields":CORE,
             "intent":"review","notes":["읽기는 초안 필드·버전·확인 상태를 변경하지 않음"]},
            {"mode":"command","text":f"맥박 {f['pulse']+3}으로 수정해.",
             "expected_fields":{"pulse":f['pulse']+3},"forbidden_fields":[],"intent":"edit",
             "notes":["잘못 읽은 숫자의 명시 정정"]},
            {"mode":"command","text":"방금 수정 취소해.","expected_fields":{},"forbidden_fields":[],
             "intent":"undo","notes":[f"상태 검토: pulse={f['pulse']+7} 관찰값으로 복원; 최초 {f['pulse']}까지 되돌리지 않음"]},
            {"mode":"answer","question_id":"temperature","text":"아직 측정하지 않았습니다.",
             "expected_fields":{},"forbidden_fields":["temperature"],
             "notes":["미측정을 0·정상값으로 생성하지 않음; 다른 필드 유지"]},
        ]
        # The second half exercises additional change/no-value and follow-up undo.
        if i >= 12:
            steps += [
                {"mode":"auto","text":"혈압이 좀 달라졌는데 새 숫자는 아직 확인 전입니다.",
                 "expected_fields":{},"forbidden_fields":["systolic_bp","diastolic_bp"],"intent":"observe",
                 "notes":["기존 혈압 유지 및 재확인 표시 기대; 새 숫자 생성 금지"]},
                {"mode":"command","text":"현재 혈압만 읽어줘.","expected_fields":{},"forbidden_fields":CORE,
                 "intent":"review","notes":["초기 질문 답에서 얻은 혈압 보존과 미확인 상태를 함께 검토"]},
            ]
        # Explicit clinical-state oracle. These are planned snapshots, not server
        # outputs. Raw extracted fields and state restoration are distinct checks.
        state = dict(initial)
        for si, step in enumerate(steps):
            if si in (1,2,3,5):
                state.update(step["expected_fields"])
            elif si == 6:
                state["pulse"] = f["pulse"] + 7
                step["undo_restores"] = {"pulse": f["pulse"] + 7}
                step["state_unchanged_except"] = ["pulse"]
            if si in (4,7,8,9):
                step["state_unchanged"] = True
            step["expected_state_subset"] = dict(state)
        flows.append({"id":f"F{i+1:03d}","category":cat,"steps":steps,
                      "notes":["단계 expected_fields는 해당 응답의 새 추출 부분집합이며 누적 세션 전체가 아님",
                               "forbidden_fields는 해당 발화에서 새로 추출하면 안 되는 필드; undo 상태 복원은 새 추출이 아님",
                               "expected_state_subset는 단계 처리 뒤 누적 세션 임상 필드의 필수 부분집합",
                               "state_unchanged는 임상 fields 불변이며 메타데이터 불변을 뜻하지 않음",
                               "undo_restores와 state_unchanged_except로 직전 edit만 되돌리고 다른 임상 필드 보존 확인",
                               "문서 질문 ID로 직접 answer API를 시험; UI 자동 질문 순서를 가정하지 않음"]})
    return flows


def validate(data: dict) -> dict:
    assert data["synthetic"] is True and data["now"] == NOW
    ids, all_text = [], []
    list_fields = {"symptoms","denied_symptoms","uncertain_symptoms"}
    def check(row, label):
        t = row["text"]
        assert isinstance(t,str) and 0 < len(t) < 4000, (label,len(t))
        assert "\x00" not in t and "\ufffd" not in t
        e, forbidden = row["expected_fields"],row["forbidden_fields"]
        assert isinstance(e,dict) and isinstance(forbidden,list)
        assert not set(e) & set(forbidden), label
        assert set(e) <= set(CORE), (label,set(e)-set(CORE))
        assert set(forbidden) <= set(FORBIDDABLE)
        for k,v in e.items():
            if k in NUMERIC:
                assert type(v) in (int,float) and math.isfinite(v), (label,k,v)
            elif k in list_fields:
                assert isinstance(v,list) and v and all(isinstance(x,str) for x in v)
                assert len(v)==len(set(v))
            elif k=="sex": assert v in ("male","female","unknown")
            elif k=="consciousness": assert v in ("alert","impaired","unresponsive","unknown")
            elif k=="breathing": assert v in ("normal","abnormal","absent","unknown")
        for a,b in (("symptoms","denied_symptoms"),("symptoms","uncertain_symptoms"),
                    ("denied_symptoms","uncertain_symptoms")):
            assert not set(e.get(a,[])) & set(e.get(b,[])), label
        all_text.append(t)
    for case in data["long_cases"]:
        ids.append(case["id"]); check(case,case["id"])
        assert "".join(case["parts"])==case["text"]
        assert all(500<=len(p)<=900 for p in case["parts"]), (case["id"],list(map(len,case['parts'])))
        assert 1800<=len(case["text"])<=3500 if case["extended"] else 400<=len(case["text"])<=1600
    for case in data["boundary_cases"]:
        ids.append(case["id"]); check(case,case["id"])
        assert case["route"] in ("dictation","followup")
        assert ("question_id" in case)==(case["route"]=="followup")
    for flow in data["flows"]:
        ids.append(flow["id"])
        assert 6<=len(flow["steps"])<=10
        for n,step in enumerate(flow["steps"]):
            check(step,f"{flow['id']}.{n+1}")
            assert step["mode"] in ("auto","dictation","command","answer")
            assert ("question_id" in step)==(step["mode"]=="answer")
            assert set(step["expected_state_subset"]) <= set(CORE)
            assert step.get("state_unchanged") in (None, True)
            if "undo_restores" in step:
                assert step["mode"]=="command" and step["intent"]=="undo"
                assert all(step["expected_state_subset"][k]==v for k,v in step["undo_restores"].items())
    assert len(ids)==len(set(ids))
    # Command/answer repetition across independent flows is intentional; complete
    # cases and initial long recordings must be unique.
    long_text = [x["text"] for x in data["long_cases"]]
    boundary_text = [x["text"] for x in data["boundary_cases"]]
    assert len(long_text)==len(set(long_text))==60
    assert len(boundary_text)==len(set(boundary_text))==240
    assert len(data["flows"])==24
    serialized_flows = [json.dumps(f["steps"],ensure_ascii=False,sort_keys=True) for f in data["flows"]]
    assert len(set(serialized_flows))==24
    lengths = [len(t) for t in long_text]
    return {"long_cases":len(long_text),"long_categories":len(set(c['category'] for c in data['long_cases'])),
            "long_chars_min":min(lengths),"long_chars_max":max(lengths),
            "long_chars_total":sum(lengths),"extended_cases":sum(c['extended'] for c in data['long_cases']),
            "reading_parts":sum(len(c['parts']) for c in data['long_cases']),
            "reading_part_chars_min":min(len(p) for c in data['long_cases'] for p in c['parts']),
            "reading_part_chars_max":max(len(p) for c in data['long_cases'] for p in c['parts']),
            "boundary_cases":len(boundary_text),"boundary_categories":dict(Counter(c['category'] for c in data['boundary_cases'])),
            "flows":len(data['flows']),"flow_steps":sum(len(c['steps']) for c in data['flows']),
            "all_text_chars":sum(map(len,all_text)),"all_text_items":len(all_text),
            "duplicate_long_texts":len(long_text)-len(set(long_text)),
            "duplicate_boundary_texts":len(boundary_text)-len(set(boundary_text)),
            "intentional_repeated_flow_utterances":len(all_text)-len(set(all_text)),
            "server_extractor_model_stt_calls":0}


def make_reading_text(data: dict, stats: dict) -> str:
    lines = ["음성 입력 장문·다양성 스트레스 시험팩 — 2026-10-30", "="*70,
             "전체 문장·환자·상황·측정값은 시험용 합성 데이터입니다. 실제 환자 이름·연락처는 없습니다.",
             "이 자료는 처치 지침·진단·응급도 정답·실제 음성인식 정확도 자료가 아닙니다.",
             "NOW=2000000000(epoch 초). 상대 발생 시각 기대는 이 고정 시각을 기준으로 계산합니다.",
             "기대값은 모델·파서 출력을 보지 않고 기획 단계의 사실로 독립 작성했습니다.",
             "긴 문장: 60개 / 상황군: 12개 / 확장판: 12개 / 경계 단문: 240개 / 다중발화: 24개.",
             "읽는 법: 본문 또는 분할부 중 한 방식을 선택합니다. 둘 다 연달아 읽으면 같은 사례를 중복 입력합니다.",
             "분할부는 전체 본문을 손실 없이 나눈 것입니다. 연속 읽기 시 본문 한 건의 시작·끝을 유지합니다.",
             "expected_fields는 필요한 정답 부분집합입니다. 명시하지 않은 추가 값은 별도 원문 근거를 검토합니다.",
             "forbidden_fields는 해당 발화에서 새로 추출할 값의 금지입니다. 기존 초안 삭제나 undo 복원 금지를 뜻하지 않습니다.",
             "흐름의 expected_state_subset는 각 단계 뒤 누적 임상 fields를, state_unchanged는 임상 fields 불변을 확인합니다.",
             "undo_restores는 직전 edit 전 값 복원, state_unchanged_except는 나머지 임상 fields 보존의 구조적 기대입니다.",
             "B098·B171~B180은 dictation 내 명시 정정의 최종 진술을 보는 신규 의미 스트레스입니다.",
             "이는 기존 speech_eval/ANNOTATION.md의 정정 dictation 제외 정책과 별도로 평가하며 기존 점수에 합산하지 않습니다.",
             "흐름의 answer는 적힌 question_id를 사용하며 자동 질문 순서를 강제하지 않습니다.",
             "가상 텍스트 시험과 실제 마이크·STT 시험 결과는 구분해 기록합니다.", "",
             "찾아가기: [L001]~[L060] 긴 설명 / [B001]~[B240] 경계 / [F001]~[F024] 흐름", "",
             "긴 설명 색인"]
    lines += [f"{c['id']} | {c['category']} | {c['presentation']} | {len(c['text'])}자 | {'확장판' if c['extended'] else '기본판'} | {len(c['parts'])}분할" for c in data['long_cases']]
    for c in data["long_cases"]:
        lines += ["", "="*70, f"[{c['id']}] {c['category']} — {c['presentation']}",
                  f"길이 {len(c['text'])}자 / 분할 {len(c['parts'])}개", "읽을 본문:",c["text"],"", "읽기 분할부:"]
        for i,p in enumerate(c["parts"],1):
            lines += [f"[{c['id']}-P{i:02d}] ({len(p)}자)",p]
        lines += ["검토 포인트:"] + ["- "+n for n in c["notes"]]
        lines += ["기대 필드: "+json.dumps(c["expected_fields"],ensure_ascii=False,sort_keys=True)]
    lines += ["", "="*70,"부록 A — 경계 단문 240개"]
    for c in data["boundary_cases"]:
        route = c['route'] + (" / question_id="+c["question_id"] if "question_id" in c else "")
        lines += ["",f"[{c['id']}] {c['category']} / {route}","읽을 본문: "+c["text"],
                  "기대 필드: "+json.dumps(c["expected_fields"],ensure_ascii=False,sort_keys=True),
                  "새 값 생성 금지: "+(", ".join(c["forbidden_fields"]) or "별도 지정 없음")]
        lines += ["검토: "+n for n in c.get("notes",[])]
    lines += ["", "="*70,"부록 B — 다중발화 흐름 24개", "각 흐름은 별도 새 세션에서 순서대로 실행합니다."]
    for f in data["flows"]:
        lines += ["",f"[{f['id']}] {f['category']} / {len(f['steps'])}턴"]
        for i,s in enumerate(f["steps"],1):
            route = s['mode']+(" / question_id="+s['question_id'] if 'question_id' in s else '')
            lines += [f"턴 {i:02d} — {route}","읽을 본문: "+s["text"],
                      "기대 필드: "+json.dumps(s["expected_fields"],ensure_ascii=False,sort_keys=True),
                      "새 값 생성 금지: "+(", ".join(s["forbidden_fields"]) or "별도 지정 없음")]
            if s.get("intent"): lines.append("기대 동작: "+s["intent"])
            lines.append("누적 상태 기대: "+json.dumps(s["expected_state_subset"],ensure_ascii=False,sort_keys=True))
            if s.get("state_unchanged"): lines.append("상태 검토: 임상 fields는 직전 단계와 동일")
            if s.get("undo_restores"):
                lines.append("취소 복원 기대: "+json.dumps(s["undo_restores"],ensure_ascii=False,sort_keys=True))
                lines.append("다른 임상 fields는 보존: "+", ".join(s["state_unchanged_except"])+"만 변경 허용")
            lines += ["검토: "+n for n in s.get("notes",[])]
    lines += ["", "="*70,"독립 정적 검증 통계",json.dumps(stats,ensure_ascii=False,indent=2),
              "SOURCESTOP: 본 시험팩 생성 완료 시점부터 원문·기대값을 고정합니다.",
              "서버/API 시험 결과는 별도 산출물에 기록하고 실패에 맞춰 이 원문·기대값을 변경하지 않습니다."]
    return "\n".join(lines)+"\n"


def main():
    data = {"schema_version":"voice-stress-independent-20261030-v1","synthetic":True,"now":NOW,
            "description":"전체 합성; 실제 환자 이름·연락처 없음. 독립 기획 기대값이며 임상 진단·처치·응급도 기대 없음.",
            "provenance":{"source":"independently_planned_synthetic_facts",
                          "field_contract":"docs/VOICE_INPUT_API.md + config/paramedic_voice_terms.json",
                          "annotation_policy":"Independent semantic stress; B098/B171-B180 explicitly include dictation corrections excluded by the older speech_eval benchmark. Report separately.",
                          "extractor_results_used":False,"server_or_stt_called":False,
                          "source_stop":"SOURCESTOP: generated sources and expectations frozen before API evaluation"},
            "long_cases":make_long(),"boundary_cases":make_boundaries(),"flows":make_flows()}
    stats = validate(data)
    data["statistics"] = stats
    OUT_JSON.parent.mkdir(parents=True,exist_ok=True)
    OUT_TEXT.parent.mkdir(parents=True,exist_ok=True)
    OUT_JSON.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    OUT_TEXT.write_text(make_reading_text(data,stats),encoding="utf-8")
    # Re-read serialized output to catch encoding or serialization regressions.
    assert validate(json.loads(OUT_JSON.read_text(encoding="utf-8"))) == stats
    result = {"statistics":stats,"files":[]}
    for p in (Path(__file__).resolve(),OUT_JSON,OUT_TEXT):
        result["files"].append({"path":str(p),"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print("SOURCESTOP")


if __name__ == "__main__":
    main()
