"""심평원 상세정보 4종 안전 조회. 기존 앱 파일 수정 없음."""
import os, urllib.request, urllib.parse, urllib.error, json
import xml.etree.ElementTree as ET
from pathlib import Path

env = Path(__file__).resolve().parent / ".env"
if env.exists():
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line=line.strip()
        if line and not line.startswith("#") and "=" in line:
            k,v=line.split("=",1)
            os.environ.setdefault(k.strip(),v.strip().strip("\\\"'"))
key=os.getenv("HIRA_API_KEY","").strip()
if not key:
    raise SystemExit("HIRA_API_KEY 없음: .env를 확인하세요.")
base_hosp="https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
# API 상세서비스의 서비스 URL이 개편될 수 있으므로 환경변수로 교체 가능
base_detail=os.getenv("HIRA_DETAIL_BASE_URL","https://apis.data.go.kr/B551182/MadmDtlInfoService2.7").rstrip("/")
operations={
    "진료과목":"getMdlrtSbjectInfoList",
    "전문의 수":"getDgsbjtPrSdrInfoList",
    "의료장비":"getMedicalEquipmentInfoList",
    "시설":"getFacilityInfo",
}
def request(url,params):
    query=urllib.parse.urlencode({"ServiceKey":key,"pageNo":1,"numOfRows":100,**params})
    req=urllib.request.Request(url+"?"+query,headers={"User-Agent":"CapstoneResearch/1.0"})
    with urllib.request.urlopen(req,timeout=20) as r:
        raw=r.read()
    try:
        root=ET.fromstring(raw)
        code=root.findtext(".//resultCode","")
        msg=root.findtext(".//resultMsg","")
        items=[{c.tag:(c.text or "").strip() for c in x} for x in root.findall(".//items/item")]
        return code,msg,items
    except ET.ParseError:
        obj=json.loads(raw)
        response=obj.get("response",{})
        header=response.get("header",{})
        body=response.get("body",{})
        its=body.get("items",{})
        if isinstance(its,dict): its=its.get("item",[])
        if isinstance(its,dict): its=[its]
        return str(header.get("resultCode","")),str(header.get("resultMsg","")),its or []
try:
    code,msg,hospitals=request(base_hosp,{"yadmNm":"분당차병원"})
except Exception as e:
    raise SystemExit("병원기본목록 조회 실패: "+type(e).__name__+" "+str(e).split("?")[0][:100])
print("병원기본목록:",code,msg,"결과",len(hospitals),"건")
matches=[h for h in hospitals if "분당차병원" in h.get("yadmNm","")]
if len(matches)!=1 or not matches[0].get("ykiho"):
    raise SystemExit("병원 일치/식별자 확인 실패. 다른 병원으로 임의 연결하지 않음.")
h=matches[0]
print("병원:",h.get("yadmNm"),"/ 주소:",h.get("addr"))
print("ykiho: 확인됨 (값은 출력하지 않음)")
for label,operation in operations.items():
    try:
        code,msg,items=request(base_detail+"/"+operation,{"ykiho":h["ykiho"]})
        print("\\n["+label+"]",operation,"결과:",code,msg,"건수:",len(items))
        for item in items[:3]:
            # 식별자 및 개인 정보는 출력하지 않음
            print({k:v for k,v in item.items() if k.lower() not in ("ykiho","servicekey")})
    except urllib.error.HTTPError as e:
        print("\\n["+label+"] HTTP 오류:",e.code,"(상세서비스 URL/활용승인/기능명 확인 필요)")
    except Exception as e:
        print("\\n["+label+"] 오류:",type(e).__name__,"(서비스 URL 또는 응답 확인 필요)")
print("\\n주의: 전문의·장비 보유 정보는 현재 응급환자 수용 가능 여부를 뜻하지 않습니다.")
