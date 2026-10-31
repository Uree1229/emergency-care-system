from pathlib import Path
root=Path(__file__).parent
src=(root/'build_proposal.py').read_text(encoding='utf-8')
extra=(root/'implementation_sections.py').read_text(encoding='utf-8')
src=src.replace("page(); h('참고자료')",extra+"\npage(); h('참고자료')")
src=src.replace('작성일 2026년 9월 7일','개정일 2026년 9월 8일')
src=src.replace("for title,desc,url in refs:","refs.extend([('[6] PostgreSQL Explicit Locking','수용 확정 트랜잭션과 행 잠금 설계 참고.','https://www.postgresql.org/docs/current/explicit-locking.html'),('[7] Google OR Tools CP SAT Solver','제약 기반 배정 모듈과 계산 상태 처리 참고.','https://developers.google.com/optimization/cp/cp_solver'),('[8] HL7 FHIR R4 Overview','병원 시스템 데이터 교환 규격 확장 검토 참고.','https://hl7.org/fhir/R4/overview.html')])\nfor title,desc,url in refs:")
src=src.replace(" pp=p(title); pp.runs[0].bold=True"," if title.startswith('[6]'): page(); h('구현 관련 참고자료')\n pp=p(title); pp.runs[0].bold=True")
src=src.replace('응급의료_연계_시스템_제안서.docx','응급의료_연계_시스템_제안서_구현방안포함.docx')
src=src.replace("d.save(ROOT/", """active=False
for pp in d.paragraphs:
 if pp.text=='16 스트레스 테스트와 개발 일정': active=True
 if pp.text=='참고자료': active=False
 if active:
  pp.paragraph_format.line_spacing=1.0
  pp.paragraph_format.space_after=Pt(3)
  if not pp.text and not pp._p.xpath('.//w:br'):
   pp.paragraph_format.space_after=Pt(0)
   pp.paragraph_format.line_spacing=Pt(1)
d.save(ROOT/""")
exec(compile(src,str(root/'build_proposal.py'),'exec'))
