"""일반 도착·외부 유입의 실제 반영 점유를 함께 검증한다. 호출자가 쓰기 트랜잭션을 연다."""
import json

from . import db

MAX_COUNT = (1 << 63) - 1


def _require(condition, message, **details):
    from .service import ServiceError
    if not condition:
        raise ServiceError('RESOURCE_CONFLICT', message, 409, **details)


def _maps(demands, epochs):
    _require(isinstance(demands, dict) and isinstance(epochs, dict) and
             demands.keys() == epochs.keys() and all(
                 isinstance(key, str) and key.strip() and type(value) is int and 0 < value <= MAX_COUNT and
                 isinstance(epochs[key], str) for key, value in demands.items()),
             '저장된 점유 기록이 올바르지 않습니다. 담당자의 정산 확인이 필요합니다.')
    return demands, epochs


def _decode(raw):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        _require(False, '저장된 점유 기록을 읽을 수 없습니다. 담당자의 정산 확인이 필요합니다.')


def patient_ledger(ledger, hid):
    _require(isinstance(ledger, dict) and type(ledger.get('version')) is int and ledger['version'] == 1 and
             ledger.get('hospital_id') == hid,
             '도착 때 기록한 병원·점유 정보가 일치하지 않습니다. 담당자의 정산 확인이 필요합니다.')
    return _maps(ledger.get('applied_resource_demands'), ledger.get('applied_resource_epochs'))


def capture_and_apply(conn, now, hid, demands):
    resources = {r['resource_type']: r for r in db.rows(conn.execute(
        'SELECT * FROM resources WHERE hospital_id=?', (hid,)))}
    applied = {key: value for key, value in demands.items() if key in resources}
    epochs = {key: resources[key]['occupancy_epoch'] for key in applied}
    _maps(applied, epochs)
    # 모든 항목을 먼저 검사해 복수 자원 중 일부만 반영되지 않도록 한다.
    for key, value in applied.items():
        occupied = resources[key]['occupied']
        _require(type(occupied) is int and 0 <= occupied <= MAX_COUNT - value,
                 '점유 수량이 저장 범위를 초과하거나 올바르지 않습니다.', resource_type=key)
    for key, value in applied.items():
        conn.execute('UPDATE resources SET occupied=occupied+?,updated_at=?,version=version+1 '
                     'WHERE hospital_id=? AND resource_type=?', (value, now, hid, key))
    return dict(version=1, hospital_id=hid, applied_resource_demands=applied, applied_resource_epochs=epochs)


def _managed_totals(conn, hid, target_epochs):
    totals = {key: 0 for key in target_epochs}

    def include(demands, epochs):
        demands, epochs = _maps(demands, epochs)
        for key in totals:
            if epochs.get(key) == target_epochs[key]:
                totals[key] += demands.get(key, 0)

    for record in conn.execute("SELECT arrival_accounting FROM patients WHERE destination_hospital_id=? "
                               "AND state='ARRIVED' AND arrival_accounting IS NOT NULL", (hid,)):
        include(*patient_ledger(_decode(record['arrival_accounting']), hid))
    for record in conn.execute("SELECT applied_resource_demands,applied_resource_epochs FROM external_arrivals "
                               "WHERE hospital_id=? AND state='ACTIVE'", (hid,)):
        include(_decode(record['applied_resource_demands']), _decode(record['applied_resource_epochs']))
    return totals


def release(conn, now, hid, demands, epochs):
    demands, epochs = _maps(demands, epochs)
    if not demands:
        return
    totals = _managed_totals(conn, hid, epochs)
    for key, demand in demands.items():
        resource = conn.execute('SELECT occupied,occupancy_epoch FROM resources '
                                'WHERE hospital_id=? AND resource_type=?', (hid, key)).fetchone()
        _require(resource is not None and type(resource['occupied']) is int and
                 resource['occupied'] >= max(demand, totals[key]) and resource['occupancy_epoch'] == epochs[key],
                 '점유 집계가 등록 이후 보정됐거나 부족합니다. 담당자의 정산 확인이 필요합니다.', resource_type=key)
    for key, demand in demands.items():
        conn.execute('UPDATE resources SET occupied=occupied-?,updated_at=?,version=version+1 '
                     'WHERE hospital_id=? AND resource_type=?', (demand, now, hid, key))
