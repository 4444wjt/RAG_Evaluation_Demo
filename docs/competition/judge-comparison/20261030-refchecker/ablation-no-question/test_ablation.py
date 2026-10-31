"""T3 offline tests. All sender responses are synthetic; upstream modules are never patched."""
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as runner
import analyze_ablation as analysis
common = runner.common


def fake_response(text='Entailment', usage=True, status=200, **message_fields):
    obj = {'model': 'synthetic-llm-no-api', 'choices': [{'message': dict(content=text, **message_fields)}]}
    if usage:
        obj['usage'] = {'prompt_tokens': 1000, 'completion_tokens': 20,
                        'prompt_cache_hit_tokens': 300, 'prompt_cache_miss_tokens': 700}
    return status, json.dumps(obj).encode('utf-8')


def synthetic_rows(inputs, target_label='Neutral'):
    reference = common.validate_reference(*inputs)
    ids = [sid for group in common.question_groups(inputs[0]) for sid in group['ids']]
    rows = []
    for r in (1, 2, 3):
        for index, sid in enumerate(ids, 1):
            label = target_label if sid in runner.SENTINELS else ('Entailment' if reference.get(sid, 1) else 'Neutral')
            rows.append({'request_id': f'Bq0{r}-{index:03}', 'arm': 'Bq0', 'round': r, 'statement_id': sid,
                         'judgment': '支持' if label == 'Entailment' else '不支持', 'original_label': label,
                         'parse_failed': False, 'interface_error': False, 'model': 'synthetic-llm-no-api',
                         'usage': json.loads(fake_response()[1])['usage']})
        for sid in runner.SENTINELS:
            rows.append({'request_id': f'S{r}-{sid}', 'arm': 'S', 'round': r, 'statement_id': sid,
                         'judgment': '支持', 'original_label': 'Entailment', 'parse_failed': False,
                         'interface_error': False, 'model': 'synthetic-llm-no-api',
                         'usage': json.loads(fake_response()[1])['usage']})
    return rows


class AblationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs, cls.preflight = runner.preflight()
        cls.groups = common.question_groups(cls.inputs[0])
        cls.loaded = runner.load_b()
        cls.requests = runner.prepared_requests(cls.groups, cls.loaded)

    @classmethod
    def tearDownClass(cls):
        runner.verify_parent()

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=runner.OUTPUT / 'tmp'))
        self.ledger = runner.AblationLedger(self.temp / 'budget-ledger.jsonl')

    def client(self, transport):
        return runner.make_client(self.temp, key='T3_SYNTHETIC_SECRET_ONLY', transport=transport)

    def run_fake(self, requests, transport):
        with patch.dict(os.environ, {'VS_ALLOW_PAID': '1'}):
            runner.execute_requests(requests, self.groups, self.loaded, self.client(transport), self.temp)

    def test_reference_and_all_237_parent_hashes(self):
        self.assertEqual(runner.verify_parent()['passed'], 237)
        self.assertEqual(len(self.preflight['input_verification']), 5)
        ref = common.validate_reference(*self.inputs)
        self.assertEqual((len(ref), sum(ref.values())), (46, 40))
        self.assertEqual(common.UNRESOLVED, {'S12', 'S14', 'S16', 'S18', 'S26', 'S35'})

    def test_request_count_order_and_ids(self):
        self.assertEqual(len(self.requests), 165)
        for r in (1, 2, 3):
            run = self.requests[(r - 1) * 55:r * 55]
            self.assertEqual([row['arm'] for row in run], ['Bq0'] * 52 + ['S'] * 3)
            self.assertEqual([row['request_id'] for row in run[:52]], [f'Bq0{r}-{i:03}' for i in range(1, 53)])
            self.assertEqual([row['request_id'] for row in run[52:]], [f'S{r}-{sid}' for sid in runner.SENTINELS])

    def test_all_no_question_prompts_and_sentinels_match_archived_prompts(self):
        originals = {row['statement_ids'][0]: row['prompt'] for row in common.read_json(runner.PARENT / 'dry-run-B.json')['requests']}
        source = runner.statement_inputs(self.groups)
        for request in self.requests:
            sid = request['statement_ids'][0]
            _, reference, question = source[sid]
            if request['arm'] == 'Bq0':
                self.assertNotIn(question, request['prompt'])
                self.assertEqual(request['prompt'], originals[sid].replace(question + ' ' + reference, reference, 1))
            else:
                self.assertEqual(request['prompt'], originals[sid])
            self.assertTrue(request['prompt_comparison_passed'])

    def test_payload_keeps_2048_non_thinking_temperature_zero(self):
        for request in self.requests:
            self.assertEqual(request['upstream_max_new_tokens'], 10)
            self.assertEqual(request['payload']['max_tokens'], 2048)
            self.assertEqual(request['payload']['temperature'], 0)
            self.assertEqual(request['payload']['thinking'], {'type': 'disabled'})

    def test_support_hypothesis_for_all_nine_neutral(self):
        summary = analysis.analyze_rows(synthetic_rows(self.inputs), self.inputs)
        self.assertEqual(summary['decision'], '支持假设')
        self.assertTrue(summary['sentinel_gate']['passed'])
        self.assertEqual([row['agree'] for row in summary['Bq0_runs']], [46, 46, 46])

    def test_support_hypothesis_for_all_nine_contradiction(self):
        summary = analysis.analyze_rows(synthetic_rows(self.inputs, 'Contradiction'), self.inputs)
        self.assertEqual(summary['decision'], '支持假设')

    def test_reject_hypothesis_for_all_nine_entailment(self):
        summary = analysis.analyze_rows(synthetic_rows(self.inputs, 'Entailment'), self.inputs)
        self.assertEqual(summary['decision'], '否定假设')

    def test_mixed_targets_are_uncertain(self):
        rows = synthetic_rows(self.inputs)
        rows[1].update(judgment='支持', original_label='Entailment')
        self.assertEqual(analysis.analyze_rows(rows, self.inputs)['decision'], '不确定')

    def test_target_parse_failure_is_uncertain_even_native_neutral(self):
        rows = synthetic_rows(self.inputs)
        rows[1].update(judgment='无结果', original_label='Neutral', parse_failed=True)
        self.assertEqual(analysis.analyze_rows(rows, self.inputs)['decision'], '不确定')

    def test_each_sentinel_can_veto_support_and_rejection(self):
        for target in ('Neutral', 'Entailment'):
            for bad_index in range(9):
                rows = synthetic_rows(self.inputs, target)
                sentinel = [row for row in rows if row['arm'] == 'S'][bad_index]
                sentinel.update(judgment='不支持', original_label='Contradiction')
                with self.subTest(target=target, sentinel=sentinel['request_id']):
                    summary = analysis.analyze_rows(rows, self.inputs)
                    self.assertEqual(summary['decision'], '存在时间漂移，不确定')
                    self.assertFalse(summary['hypothesis_evaluated'])

    def test_sentinel_no_result_is_drift_even_native_entailment(self):
        rows = synthetic_rows(self.inputs)
        rows[52].update(judgment='无结果', parse_failed=True)
        self.assertEqual(analysis.analyze_rows(rows, self.inputs)['decision'], '存在时间漂移，不确定')

    def test_other_statements_cannot_change_hypothesis_decision(self):
        rows = synthetic_rows(self.inputs)
        for row in rows:
            if row['arm'] == 'Bq0' and row['statement_id'] not in runner.SENTINELS:
                row.update(judgment='无结果', parse_failed=True)
        self.assertEqual(analysis.analyze_rows(rows, self.inputs)['decision'], '支持假设')

    def test_flips_missing_and_six_unresolved_are_kept_separate(self):
        rows = synthetic_rows(self.inputs)
        rows[55].update(judgment='不支持', original_label='Neutral')
        rows[110].update(judgment='无结果', parse_failed=True)
        summary = analysis.analyze_rows(rows, self.inputs)
        self.assertEqual(summary['binary_flip_ids'], ['S01'])
        self.assertEqual(summary['any_no_result_ids'], ['S01'])
        self.assertEqual(set(summary['unresolved']), common.UNRESOLVED)
        self.assertTrue(all(len(rounds) == 3 for rounds in summary['unresolved'].values()))
        self.assertEqual([row['denominator'] for row in summary['Bq0_runs']], [46] * 3)

    def test_duplicate_incomplete_and_wrong_id_results_stop(self):
        rows = synthetic_rows(self.inputs)
        wrong = deepcopy(rows)
        wrong[0]['request_id'] = 'Bq01-999'
        for malformed in (rows + [rows[0]], rows[:-1], wrong):
            with self.subTest(count=len(malformed)), self.assertRaises(common.StopRun):
                analysis.analyze_rows(malformed, self.inputs)

    def test_bad_reference_stops_before_analysis(self):
        inputs = deepcopy(self.inputs)
        inputs[3]['counts']['resolved'] = 45
        with self.assertRaisesRegex(common.StopRun, 'ARCHIVED_COUNTS_DISAGREE'):
            analysis.analyze_rows(synthetic_rows(self.inputs), inputs)

    def test_missing_usage_remains_unknown(self):
        rows = synthetic_rows(self.inputs)
        rows[0].update(usage=None, model=None)
        summary = analysis.analyze_rows(rows, self.inputs)
        self.assertIsNone(summary['estimated_total_cny'])
        self.assertIsNone(summary['usage_total'])
        self.assertEqual(summary['unknown_cost_request_count'], 1)
        self.assertEqual(summary['missing_model_request_count'], 1)

    def test_exactly_one_yuan_stops_before_writing_reservation(self):
        amount = common.reservation('x')
        common.append_jsonl(self.ledger.path, {'event': 'reserve', 'request_id': 'pending', 'reserved_cny': str(Decimal('1') - amount)})
        before = self.ledger.path.read_bytes()
        with self.assertRaisesRegex(common.StopRun, 'BUDGET_HARD_STOP'):
            self.ledger.reserve('next', 'x', 'Bq0')
        self.assertEqual(before, self.ledger.path.read_bytes())

    def test_settled_plus_pending_plus_next_reaches_one_yuan(self):
        amount = common.reservation('x')
        common.append_jsonl(self.ledger.path, {'event': 'reserve', 'request_id': 'settled', 'reserved_cny': '0.4'})
        common.append_jsonl(self.ledger.path, {'event': 'settle', 'request_id': 'settled', 'actual_estimated_cny': '0.4'})
        common.append_jsonl(self.ledger.path, {'event': 'reserve', 'request_id': 'pending', 'reserved_cny': str(Decimal('0.6') - amount)})
        with self.assertRaisesRegex(common.StopRun, 'BUDGET_HARD_STOP'):
            self.ledger.reserve('next', 'x', 'Bq0')

    def test_settlement_releases_reserve_even_total_reservations_exceed_one(self):
        usage = json.loads(fake_response()[1])['usage']
        for i in range(70):
            self.ledger.reserve(str(i), 'x', 'Bq0')
            self.ledger.settle(str(i), usage)
        self.assertGreater(common.reservation('x') * 70, Decimal('1'))
        self.assertEqual(self.ledger.totals(), (Decimal('0.11004000'), Decimal('0')))

    def test_unknown_usage_keeps_pending_reservation(self):
        amount = self.ledger.reserve('one', 'x', 'Bq0')
        with self.assertRaises(ValueError):
            self.ledger.settle('one', None)
        self.assertEqual(self.ledger.totals(), (Decimal('0'), amount))

    def test_duplicate_reservation_rejected(self):
        self.ledger.reserve('one', 'x', 'Bq0')
        with self.assertRaisesRegex(common.StopRun, 'REQUEST_ALREADY_RECORDED'):
            self.ledger.reserve('one', 'x', 'Bq0')

    def test_interface_error_rate_strictly_over_ten_percent_stops(self):
        for i in range(9):
            self.ledger.outcome(str(i), 'Bq0', False)
        self.ledger.outcome('ten', 'Bq0', True)
        with self.assertRaisesRegex(common.StopRun, 'INTERFACE_ERROR_RATE'):
            self.ledger.outcome('eleven', 'Bq0', True)

    def test_cli_run_without_paid_flag_refuses_before_files_or_key(self):
        with patch.dict(os.environ):
            os.environ.pop('VS_ALLOW_PAID', None)
            result = subprocess.run([sys.executable, '-I', '-S', '-B', str(runner.OUTPUT / 'run_ablation.py'), '--run'],
                                    capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        self.assertIn('PAID_DISABLED', result.stderr)
        self.assertNotIn('已读取', result.stdout)

    def test_no_key_never_falls_back_to_env_file(self):
        with patch.dict(os.environ):
            os.environ.pop('OPENAI_API_KEY', None)
            with self.assertRaisesRegex(common.StopRun, 'PROCESS_OPENAI_API_KEY_REQUIRED'):
                runner.make_client(self.temp)

    def test_unparseable_fake_llm_keeps_native_neutral_but_outer_no_result(self):
        self.run_fake(self.requests[:1], lambda payload, key: fake_response('No usable label.'))
        row = common.read_jsonl(self.temp / 'results.jsonl')[0]
        self.assertEqual((row['judgment'], row['original_label'], row['parse_failed']), ('无结果', 'Neutral', True))
        raw = common.read_json(self.temp / row['raw_path'])
        self.assertEqual(raw['native_parser']['return'], [[['Neutral']]])

    def test_native_contradiction_and_entailment_are_preserved(self):
        texts = iter(('Contradiction', 'entailment'))
        self.run_fake(self.requests[:2], lambda payload, key: fake_response(next(texts)))
        rows = common.read_jsonl(self.temp / 'results.jsonl')
        self.assertEqual([row['judgment'] for row in rows], ['不支持', '支持'])

    def test_empty_fake_response_records_no_result_then_stops_for_interface_error(self):
        with self.assertRaisesRegex(common.StopRun, 'INTERFACE_ERROR_RATE'):
            self.run_fake(self.requests[:1], lambda payload, key: fake_response(''))
        row = common.read_jsonl(self.temp / 'results.jsonl')[0]
        self.assertEqual(row['judgment'], '无结果')
        self.assertTrue(row['parse_failed'])
        self.assertTrue(row['interface_error'])

    def test_transport_error_is_not_retried_and_keeps_reserve(self):
        calls = []
        def failing(payload, key):
            calls.append(1)
            raise OSError('synthetic error')
        with self.assertRaisesRegex(common.StopRun, 'INTERFACE_ERROR_RATE'):
            self.run_fake(self.requests[:1], failing)
        self.assertEqual(calls, [1])
        self.assertGreater(self.client(failing).ledger.totals()[1], Decimal('0'))

    def test_thinking_content_stops_without_retry(self):
        with self.assertRaisesRegex(common.StopRun, 'THINKING_CONTENT_DETECTED'):
            self.run_fake(self.requests[:1], lambda payload, key: fake_response(reasoning_content='synthetic reasoning'))

    def test_secret_echo_is_redacted_and_stops(self):
        with self.assertRaisesRegex(common.StopRun, 'SECRET_ECHO'):
            self.run_fake(self.requests[:1], lambda payload, key: fake_response(key))
        raw = (self.temp / 'raw' / (self.requests[0]['request_id'] + '.json')).read_text(encoding='utf-8')
        self.assertNotIn('T3_SYNTHETIC_SECRET_ONLY', raw)

    def test_scoped_guard_blocks_parent_write_without_creating_file(self):
        forbidden = runner.PARENT / 'T3-forbidden-write.txt'
        with self.assertRaisesRegex(common.StopRun, 'OUT_OF_SCOPE_WRITE'):
            forbidden.write_text('synthetic attempt', encoding='utf-8')
        self.assertFalse(forbidden.exists())

    def test_guard_blocks_env_open_before_reading(self):
        with self.assertRaisesRegex(common.StopRun, 'ENV_READ_FORBIDDEN'):
            (self.temp / '.env').open('r')

    def test_guard_blocks_dns_before_network(self):
        with self.assertRaisesRegex(common.StopRun, 'OFFLINE_NETWORK_ATTEMPT'):
            socket.getaddrinfo('t3-offline.invalid', 443)

    def test_worst_case_reservation_uses_remaining_not_one_yuan(self):
        total = sum((Decimal(row['reserved_cny']) for row in self.requests), Decimal('0'))
        self.assertGreater(total, runner.HARD_LIMIT)
        self.assertLessEqual(total, runner.REMAINING_AUTHORIZATION)
        self.assertLess(max(Decimal(row['reserved_cny']) for row in self.requests), runner.HARD_LIMIT)
        estimate = runner.estimate_actual_cost(self.requests)
        self.assertEqual(len(estimate['matched_requests']), 165)
        self.assertLess(Decimal(estimate['estimated_total_cny']), Decimal('1'))

    def test_full_165_fake_requests_and_actual_analysis_cli(self):
        calls = []
        def transport(payload, key):
            request = self.requests[len(calls)]
            calls.append(payload)
            sid = request['statement_ids'][0]
            label = 'Neutral' if request['arm'] == 'Bq0' and sid in runner.SENTINELS else 'Entailment'
            return fake_response(label)
        self.run_fake(self.requests, transport)
        self.assertEqual(len(calls), 165)
        rows = common.read_jsonl(self.temp / 'results.jsonl')
        self.assertEqual([row['request_id'] for row in rows], [row['request_id'] for row in self.requests])
        self.assertEqual(len(list((self.temp / 'raw').glob('*.json'))), 165)
        self.assertEqual(self.client(transport).ledger.totals(), (Decimal('0.25938000'), Decimal('0')))
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(runner.OUTPUT / 'analyze_ablation.py'),
                                 '--results', str(self.temp / 'results.jsonl'), '--output-dir', str(self.temp)],
                                capture_output=True, text=True, encoding='utf-8')
        (self.temp / 'analyze-cli.log').write_text('exit_code=' + str(result.returncode) + '\n' + result.stdout + result.stderr, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = common.read_json(self.temp / 'summary.json')
        self.assertEqual(summary['decision'], '支持假设')
        self.assertEqual(summary['request_count'], 165)
        self.assertTrue((self.temp / 'report.md').is_file())


if __name__ == '__main__':
    runner.configure(offline=True)
    unittest.main(verbosity=2)
