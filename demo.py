from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from capture.markers import record_start
from capture.requests import count_request
from capture.station_evidence import capture_notice, capture_reference, protect_reference_cuts
from history.reconcile import reconcile_item
from history.retrieve import retrieve
from history.shift_index import list_shift_reports
from intake.opening_stock import initialize_opening_stock
from inventory.issue import issue
from movement.live_location import locate
from movement.recording import LocalRecording
from storage import connect


def run_demo(path, recording):
    shift = recording['shift_id']
    reference, remote = recording['reference'], recording['remote']
    count_id = 'shift-handover'
    with closing(connect(path)) as db:
        initialize_opening_stock(db, shift, reference, remote, recording['opening'])
        source = LocalRecording(db, shift, recording['steps'], before_arrival=protect_reference_cuts)
        count_request(db, count_id, shift, reference, remote)
        record_start(db, count_id, remote)
        released = source.advance().action
        capture_reference(db, count_id)
        capture_notice(db, count_id)
        issued = issue(db, count_id)
        if issued.status != 'issued':
            raise RuntimeError(f'count did not issue: {issued.reason}')
        source.advance()
        live_station = locate(db, shift, released.item_id).station
        source.close_source()
    with closing(connect(path)) as db:
        fixed, receipt = retrieve(db, count_id)
        history = list_shift_reports(db, shift)
        search = reconcile_item(db, count_id, 'X')
        actual = {name: [getattr(fixed.totals, name).grams,
                         [line.item_id for line in fixed.items_in(name)]]
                  for name in ('reference', 'remote', 'transit')}
        actual['total_grams'] = fixed.totals.total_grams
        downstream = {'category': search.entry.category, 'grams': search.entry.grams,
                      'release_origin': search.location_origin, 'live_station': live_station,
                      'original_receipt': receipt == issued.receipt, 'history': list(history)}
    expected = {'reference': [0, []], 'remote': [11, ['Y']], 'transit': [7, ['X']], 'total_grams': 18}
    expected_downstream = {'category': 'transit', 'grams': 7, 'release_origin': 'release:day:41',
                           'live_station': 'B', 'original_receipt': True, 'history': ['shift-handover']}
    if actual != expected or downstream != expected_downstream or fixed != issued.report:
        raise RuntimeError(f'handover mismatch: {actual!r} / {downstream!r}')
    print(f'甲点 {actual["reference"][0]} g，乙点 {actual["remote"][0]} g，'
          f'途中 {actual["transit"][0]} g，合计 {actual["total_grams"]} g')
    print(f'{fixed.count_id} 首执 {receipt.receipt_id}，X 放行出处 {search.location_origin}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v5.transit_total',
        'input': {'recording': recording, 'schedule': ['B start', 'X release', 'A cut',
                                                       'B notice', 'issue', 'X arrival', 'reopen']},
        'expected': expected,
        'actual': actual,
        'downstream_expected': expected_downstream,
        'downstream_actual': downstream,
        'code_paths': ['capture/station_evidence.py', 'inventory/evidence.py', 'inventory/custody.py',
                       'inventory/weights.py', 'inventory/report_store.py', 'history/reconcile.py'],
        'oracle': '手算。7 g 的 X 已离甲但乙截点先于点收，11 g 的 Y 从未离乙。点收完成后在线落乙，离线复领仍按原截面。',
    }, ensure_ascii=False, sort_keys=True))


def main():
    resource = Path(__file__).with_name('samples') / 'notice_first.json'
    recording = json.loads(resource.read_text(encoding='utf-8'))
    with TemporaryDirectory() as directory:
        run_demo(Path(directory) / 'handover.sqlite', recording)


if __name__ == '__main__':
    main()
