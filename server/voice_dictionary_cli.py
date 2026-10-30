"""Maintain local voice dictionaries; no external services or patient writes.

Examples:
  python -m server.voice_dictionary_cli --db data/demo.db status
  python -m server.voice_dictionary_cli --db data/demo.db search --query 흉통
  python -m server.voice_dictionary_cli --db data/demo.db import-kostom --file terms.csv --version 7.0
"""
import argparse
import json
from pathlib import Path

from . import db, service, voice_lexicon


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True,help='Existing server SQLite DB; existing patients are preserved')
    commands=parser.add_subparsers(dest='action',required=True)
    commands.add_parser('status')
    search=commands.add_parser('search')
    search.add_argument('--query',required=True)
    search.add_argument('--source-id')
    search.add_argument('--limit',type=int,default=20)
    for kind in ('kostom','hira'):
        child=commands.add_parser('import-'+kind)
        child.add_argument('--file',required=True,help='Local CSV; no download is made')
        child.add_argument('--version',required=True,help='Actual publication version, never assumed latest')
    alias=commands.add_parser('add-alias')
    alias.add_argument('--source-id',required=True,choices=(voice_lexicon.PROJECT_TERMS,
        voice_lexicon.PROJECT_R1,voice_lexicon.PROJECT_COMMANDS))
    alias.add_argument('--code',required=True)
    alias.add_argument('--alias',required=True)
    alias.add_argument('--reviewed-by',required=True)
    alias.add_argument('--expected-version')
    args=parser.parse_args()
    if not Path(args.db).is_file():
        parser.error('--db must name an existing server database; initialize it with server.app first')
    conn=db.connect(args.db)
    try:
        db.init(conn)
        if args.action=='status':
            result=voice_lexicon.overview(conn)
        elif args.action=='search':
            result=voice_lexicon.search(conn,args.query,limit=args.limit,source_id=args.source_id)
        elif args.action=='add-alias':
            result=voice_lexicon.add_alias(conn,args.source_id,args.code,args.alias,args.reviewed_by,
                                           version=args.expected_version)
        elif args.action=='import-kostom':
            result=voice_lexicon.import_reference_csv(conn,args.file,
                source_id='kostom.diagnosis',version=args.version,title='보건복지부 보건의료용어표준 KOSTOM 진단',
                url='https://www.hins.or.kr/menu/viewMenu.do?menuNo=4050100',license='공공누리 제1유형: 출처표시',
                code_column='용어코드',korean_column='한글명',english_column='영문명',
                extra_columns={'concept_code':'개념코드'})
        else:
            result=voice_lexicon.import_reference_csv(conn,args.file,
                source_id='hira.disease_master',version=args.version,title='건강보험심사평가원 상병마스터',
                url='https://www.data.go.kr/data/15067467/fileData.do',license='공공누리 제1유형: 출처표시',
                code_column='상병기호',korean_column='한글명',english_column='영문명',encoding='cp949')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except service.ServiceError as error:
        print(json.dumps(error.to_dict(),ensure_ascii=False))
        raise SystemExit(1)
    finally:
        conn.close()


if __name__=='__main__':
    main()
