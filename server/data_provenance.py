"""Read-only provenance inventory; saved observations never become capacity.

CLI: python -m server.data_provenance inventory --db data/public.db --output NEW.json
CLI: python -m server.data_provenance fixture --source data/public.db --output NEW-DIR
The fixture copies only public hospital/reference records, creates explicitly
synthetic resources, and never copies patients/inquiries or calls an API.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
SCOPE_PATH=ROOT/'config/resource_model_scope_20261030.json'
CATALOG_PATH=ROOT/'config/treatment_requirements.json'
CONTRACT='data-provenance-20261030-v1'


def _load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(value,default):
    try:return json.loads(value) if isinstance(value,str) else value
    except (TypeError,ValueError):return default


def _utc(value):
    if value is None:return None
    try:return datetime.fromtimestamp(value,timezone.utc).isoformat()
    except (ValueError,TypeError,OverflowError,OSError):return None


def _rows(conn,sql,args=()):
    cursor=conn.execute(sql,args)
    keys=[c[0] for c in cursor.description]
    return [dict(zip(keys,row)) for row in cursor.fetchall()]


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _freshness(observed,now,limit=600):
    if type(observed) is not int or observed<0 or _utc(observed) is None:observed=None
    return dict(as_of_epoch=observed,as_of_utc=_utc(observed),
        age_seconds=None if observed is None else now-observed,
        time_status='unknown' if observed is None else ('future' if observed>now else ('stale' if now-observed>limit else 'recent')),
        stale=None if observed is None else observed>now or now-observed>limit)


def resource_scope():
    from .solver_adapter import RESOURCE_TYPES
    scope=_load(SCOPE_PATH)
    if list(RESOURCE_TYPES)!=scope['model_resources']:
        raise ValueError('resource scope manifest differs from active solver adapter')
    catalog=_load(CATALOG_PATH);profiles=[]
    for profile in catalog['profiles']:
        demands={}
        for group in ('resource_demands','extended_resource_demands'):
            for key,value in profile.get(group,{}).items():demands[key]=max(demands.get(key,0),value)
        unsupported=sorted(set(demands)-set(RESOURCE_TYPES))
        profiles.append(dict(id=profile['id'],label=profile.get('label'),resource_demands=demands,
            unsupported_resources=unsupported,scheduling_supported=not unsupported,
            unsupported_action='NEEDS_REVIEW' if unsupported else None))
    return {**scope,'profiles':profiles,'catalog':dict(catalog_date=catalog.get('catalog_date'),
        catalog_status=catalog.get('catalog_status'),sha256=_sha(CATALOG_PATH)),'scope_sha256':_sha(SCOPE_PATH)}


def _origin(category,source,scope):
    return dict(classification=category,label=scope['ui_labels'].get(category,scope['ui_labels']['unknown']),
        stored_source=source,verified_real_hospital_measurement=False,
        evidence_scope='stored labels and timestamps; collector identity/clinical truth not independently verified')


@contextmanager
def _read_snapshot(conn):
    """Nest a deferred read snapshot without taking a writer reservation."""
    name='provenance_view_'+uuid.uuid4().hex
    conn.execute('SAVEPOINT '+name)
    try:
        yield
    except BaseException:
        conn.execute('ROLLBACK TO '+name)
        raise
    finally:
        conn.execute('RELEASE '+name)


def inventory(conn,now=None,hospital_id=None):
    """One read snapshot; caller keeps its connection and any outer transaction.

    Does not call db.init, migrations, external APIs, service writes or PRAGMAs.
    No raw snapshot payload, patient identifier, contact, actor or API key is returned.
    """
    with _read_snapshot(conn):
        return _inventory(conn,now,hospital_id)


def _inventory(conn,now=None,hospital_id=None):
    now=int(time.time()) if now is None else int(now)
    scope=resource_scope();tables=_tables(conn)
    if 'hospitals' not in tables:
        return dict(schema_version=1,contract=CONTRACT,as_of_utc=_utc(now),hospitals=[],resource_model_scope=scope,
            unavailable=['hospitals table missing'],external_api_calls=0,read_only=True)
    sql='SELECT * FROM hospitals';args=()
    if hospital_id is not None:sql+=' WHERE id=?';args=(hospital_id,)
    hospitals=_rows(conn,sql+' ORDER BY id',args);result=[]
    for hospital in hospitals:
        hid=hospital['id'];source=hospital.get('source','unknown')
        identity_class={'public':'public_identity_frozen','synthetic':'synthetic_identity'}.get(source,'unknown')
        snapshots=_rows(conn,'SELECT hospital_id,endpoint,source_observed_at,fetched_at,normalized FROM public_api_snapshots WHERE hospital_id=? ORDER BY endpoint',(hid,)) if 'public_api_snapshots' in tables else []
        observations=[]
        for snapshot in snapshots:
            endpoint=snapshot['endpoint']
            category='public_observation' if endpoint=='nmc_rltm_beds' else 'registered_reference'
            if endpoint in ('nmc_severe_accept','nmc_acceptance_messages'):category='public_observation'
            normalized=_json(snapshot['normalized'],{}) or {}
            if not isinstance(normalized,dict):normalized={}
            summaries=[]
            for group in ('resources','facilities','equipment','service_availability'):
                values=normalized.get(group,[])
                if isinstance(values,list):
                    for value in values:
                        if isinstance(value,dict):
                            # Values are evidence references, never returned as operating capacity.
                            summaries.append({k:v for k,v in value.items() if k in
                                ('resource_type','facility_type','equipment_type','service_type','source_field',
                                 'reported_available','reported_count','status')})
            observations.append(dict(endpoint=endpoint,origin=_origin(category,'public',scope),
                **_freshness(snapshot['source_observed_at'],now),
                fetched_at=snapshot['fetched_at'],fetched_at_utc=_utc(snapshot['fetched_at']),
                collection_is_not_observation_time=snapshot['source_observed_at'] is None,
                saved_snapshot=True,refreshed_this_request=False,
                evidence=summaries,normalized_sha256=hashlib.sha256(snapshot['normalized'].encode()).hexdigest(),
                operating_capacity_projection=False,patient_acceptance_confirmed=False))
        resources=_rows(conn,'SELECT * FROM resources WHERE hospital_id=? ORDER BY resource_type',(hid,)) if 'resources' in tables else []
        operating=[]
        for resource in resources:
            stored=resource.get('source','unknown')
            category='synthetic_fixture' if stored=='synthetic' else (
                'manual_demo_entry' if stored=='hospital_input' and source=='synthetic' else
                'manual_unverified_operation' if stored=='hospital_input' else 'unknown')
            unsupported=resource['resource_type'] not in scope['model_resources']
            operating.append(dict(resource_type=resource['resource_type'],capacity=resource['capacity'],
                occupied=resource['occupied'],status=resource.get('status'),origin=_origin(category,stored,scope),
                **_freshness(resource.get('source_observed_at'),now),
                updated_at=resource.get('updated_at'),updated_at_utc=_utc(resource.get('updated_at')),
                update_freshness=_freshness(resource.get('updated_at'),now),
                capacity_is_public_observation=False if category in ('synthetic_fixture','manual_demo_entry','manual_unverified_operation') else None,
                public_source_in_operating_table_requires_review=stored=='public',
                scheduling_model_supported=not unsupported,
                unsupported_action='NEEDS_REVIEW' if unsupported else None,
                quantity_semantics='operating-table input; not public registered/available count; not patient-specific acceptance'))
        states={}
        if 'inquiries' in tables:
            states={r['state']:r['count'] for r in _rows(conn,'SELECT state,COUNT(*) AS count FROM inquiries WHERE hospital_id=? GROUP BY state',(hid,))}
        newest=max((s['fetched_at'] for s in snapshots),default=None)
        result.append(dict(hospital_id=hid,name=hospital.get('name'),identity=dict(origin=_origin(identity_class,source,scope),
            source_ids=_json(hospital.get('source_ids'),{}),last_collection_at=newest,last_collection_utc=_utc(newest)),
            public_evidence=observations,operating_resources=operating,
            acceptance_scope=dict(inventory_is_patient_acceptance=False,stored_inquiry_state_counts=states,
                state_counts_are_not_current_valid_acceptances=True,
                confirmation='Patient version, expiry, current valid response and staff confirmation must be checked by existing workflow'),
            mixed_public_identity_and_synthetic_operations=source=='public' and any(r['origin']['classification']=='synthetic_fixture' for r in operating)))
    fixture=None
    if 'demo_provenance' in tables:
        entries=_rows(conn,"SELECT value FROM demo_provenance WHERE key='manifest'")
        fixture=_json(entries[0]['value'],{}) if entries else None
    return dict(schema_version=1,contract=CONTRACT,as_of_epoch=now,as_of_utc=_utc(now),
        read_only=True,external_api_calls=0,hospitals=result,fixture_context=fixture,
        summary=dict(hospitals=len(result),identity_classes=dict(Counter(h['identity']['origin']['classification'] for h in result)),
            operating_resource_classes=dict(Counter(r['origin']['classification'] for h in result for r in h['operating_resources'])),
            public_snapshots=sum(len(h['public_evidence']) for h in result)),
        resource_model_scope=scope,
        limitations=['Saved source labels do not prove actual staff entry or current truth',
            'Public reference/observations never establish patient-specific acceptance',
            'No resource, source date or capacity is refreshed by this read-only request'])


def read_inventory(path,now=None,hospital_id=None):
    path=Path(path).resolve()
    if not path.is_file():raise FileNotFoundError('Existing database required; no automatic creation')
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        conn.execute('BEGIN')
        return inventory(conn,now,hospital_id)
    finally:conn.close()


def create_fixture(source_path,output_dir,now=None,capacity=4,teams=1):
    """New dedicated DB only; public identity/reference + synthetic operations."""
    from . import db
    source_path=Path(source_path).resolve();output_dir=Path(output_dir).resolve()
    if output_dir.exists():raise FileExistsError('Use a new directory; existing DBs are never overwritten')
    if not source_path.is_file():raise FileNotFoundError('Existing public-source DB required')
    if any(type(v) is not int or v<0 for v in (capacity,teams)):raise ValueError('nonnegative exact resource quantities required')
    now=int(time.time()) if now is None else int(now)
    source=sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True)
    try:
        source.execute('BEGIN')
        hospitals=_rows(source,"SELECT * FROM hospitals WHERE source='public' ORDER BY id")
        if not hospitals:raise ValueError('No public hospital identity to copy')
        snapshots=_rows(source,'SELECT * FROM public_api_snapshots ORDER BY hospital_id,endpoint') if 'public_api_snapshots' in _tables(source) else []
        ids={h['id'] for h in hospitals};snapshots=[s for s in snapshots if s['hospital_id'] in ids]
    finally:source.close()
    output_dir.mkdir(parents=True);target_path=output_dir/'public-identity-synthetic-operations.db'
    with target_path.open('xb'):pass
    target=db.connect(str(target_path))
    try:
        db.init(target)
        target.execute('BEGIN IMMEDIATE')
        for h in hospitals:
            keys=('id','name','address','phone','lat','lon','capabilities','source','source_ids','capabilities_status')
            values=[h.get(k,'unknown' if k=='capabilities_status' else None) for k in keys]
            target.execute('INSERT INTO hospitals ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',values)
            for kind,quantity in (('ed_bed',capacity),('special_team',teams)):
                target.execute("INSERT INTO resources(hospital_id,resource_type,capacity,occupied,status,source,source_observed_at,updated_at,version) VALUES(?,?,?,0,'available','synthetic',NULL,?,1)",(h['id'],kind,quantity,now))
        for item in snapshots:
            keys=('hospital_id','endpoint','source_observed_at','fetched_at','normalized','raw_data')
            target.execute('INSERT INTO public_api_snapshots VALUES(?,?,?,?,?,?)',[item[k] for k in keys])
        manifest=dict(id='public-identity-synthetic-operations-20261030-v1',created_at=now,created_at_utc=_utc(now),
            public_identity_source=source_path.name,public_identity_source_main_db_sha256=_sha(source_path),
            copied_identity_reference_projection_sha256=hashlib.sha256(json.dumps(dict(hospitals=hospitals,snapshots=snapshots),ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            main_db_hash_scope='Main file only; copied projection digest binds the read-transaction rows including WAL-visible records',
            hospital_count=len(hospitals),public_snapshot_count=len(snapshots),
            synthetic_operating_capacity=dict(ed_bed=capacity,special_team=teams),
            public_observation_dates_preserved=True,operating_capacity_from_public_data=False,
            copied_patients=0,copied_inquiries=0,external_api_calls=0,clinical_or_hospital_staff_validation=False)
        target.execute('CREATE TABLE demo_provenance(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        target.execute('INSERT INTO demo_provenance VALUES(?,?)',('manifest',json.dumps(manifest,ensure_ascii=False)))
        target.commit()
        view=inventory(target,now)
        _write_json(output_dir/'manifest.json',manifest);_write_json(output_dir/'inventory.json',view)
    except Exception:
        target.rollback()
        _write_json(output_dir/'failure.json',dict(passed=False,partial_artifacts_preserved=True))
        raise
    finally:target.close()
    return dict(database=str(target_path),manifest=manifest)


def _write_json(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    inspect=commands.add_parser('inventory');inspect.add_argument('--db',type=Path,required=True)
    inspect.add_argument('--output',type=Path,required=True);inspect.add_argument('--hospital-id')
    fixture=commands.add_parser('fixture');fixture.add_argument('--source',type=Path,required=True)
    fixture.add_argument('--output',type=Path,required=True)
    fixture.add_argument('--capacity',type=int,default=4);fixture.add_argument('--teams',type=int,default=1)
    args=parser.parse_args(argv)
    if args.command=='inventory':
        if args.output.exists():raise FileExistsError('Use a new output file')
        value=read_inventory(args.db,hospital_id=args.hospital_id);_write_json(args.output,value)
        print(json.dumps(value.get('summary',{}),ensure_ascii=False))
    else:print(json.dumps(create_fixture(args.source,args.output,capacity=args.capacity,teams=args.teams),ensure_ascii=False))


if __name__=='__main__':main()
