"""Synthetic LLM tests only. Each interpreter tests its own native arm plus shared logic."""
import argparse
from contextlib import redirect_stderr
from copy import deepcopy
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_common as common
import run_compare as runner
import analyze


def response(text='Entailment', **message_fields):
    obj = {'model': 'synthetic-model', 'choices': [{'message': dict(content=text, **message_fields)}],
           'usage': {'prompt_tokens': 1000, 'completion_tokens': 20, 'prompt_cache_hit_tokens': 300,
                     'prompt_cache_miss_tokens': 700}}
    return 200, json.dumps(obj).encode('utf-8')


def synthetic_rows(inputs, scores=None):
    reference = common.validate_reference(*inputs)
    groups = common.question_groups(inputs[0])
    group_index = {sid: i for i, group in enumerate(groups, 1) for sid in group['ids']}
    supported_ids = sorted(sid for sid, label in reference.items() if label == 1)
    rows = []
    for arm in ('A', 'B'):
        for round_number in (1, 2, 3):
            score = (scores or {}).get(arm, [46, 46, 46])[round_number - 1]
            misses = set(supported_ids[:46-score])
            for index in range(1, 53):
                sid = f'S{index:02}'
                value = reference.get(sid, 1)
                if sid in misses:
                    value = 1-value
                label = value if arm == 'A' else ('Entailment' if value else 'Neutral')
                request_index = group_index[sid] if arm == 'A' else index
                rows.append({'request_id': f'{arm}{round_number}-{request_index:03}', 'arm': arm, 'round': round_number,
                             'statement_id': sid, 'judgment': '支持' if value else '不支持', 'original_label': label,
                             'parse_failed': False, 'interface_error': False, 'model': 'synthetic-model',
                             'usage': {'prompt_tokens': 10, 'completion_tokens': 1, 'prompt_cache_hit_tokens': 2}})
    return rows


class CommonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = common.load_inputs()

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='fake-', dir=common.TASK / 'T1-tmp'))
        self.ledger = common.BudgetLedger(self.temp / 'ledger.jsonl')

    def client(self, transport):
        return runner.DeepSeekClient(self.ledger, self.temp / 'raw', transport=transport, key='T1_FAKE_SECRET_ONLY')

    def test_five_git_blobs(self):
        self.assertEqual(len(common.verify_inputs()), 5)

    def test_reference_46_40_6_and_exact_unresolved(self):
        reference = common.validate_reference(*self.inputs)
        self.assertEqual((len(reference), sum(reference.values())), (46, 40))
        self.assertEqual({f'S{i:02}' for i in range(1, 53)} - reference.keys(), common.UNRESOLVED)

    def test_reference_wrong_unresolved_stops(self):
        inputs = deepcopy(self.inputs)
        inputs[2]['labels']['S12']['label'] = '支持'
        with self.assertRaisesRegex(common.StopRun, 'REFERENCE_SET'):
            common.validate_reference(*inputs)

    def test_archived_reference_disagreement_stops(self):
        inputs = deepcopy(self.inputs)
        inputs[3]['rows'][0]['human'] = 0
        with self.assertRaisesRegex(common.StopRun, 'ARCHIVED_ANALYSIS_DISAGREES'):
            common.validate_reference(*inputs)

    def test_reservation_exact_formula(self):
        self.assertEqual(common.reservation('中ab'), Decimal('0.016396'))

    def test_budget_exactly_at_8_stops_before_reserve(self):
        amount = common.reservation('x')
        common.append_jsonl(self.ledger.path, {'event': 'reserve', 'request_id': 'pending', 'reserved_cny': str(Decimal('8')-amount)})
        before = self.ledger.path.read_bytes()
        with self.assertRaisesRegex(common.StopRun, 'BUDGET_HARD_STOP'):
            self.ledger.reserve('new', 'x', 'A')
        self.assertEqual(before, self.ledger.path.read_bytes())

    def test_budget_settlement_releases_only_own_reserve(self):
        self.ledger.reserve('one', 'x', 'A')
        pending = self.ledger.reserve('two', 'xx', 'B')
        usage = json.loads(response()[1])['usage']
        self.assertEqual(self.ledger.settle('one', usage), Decimal('0.001572'))
        self.assertEqual(self.ledger.totals(), (Decimal('0.001572'), pending))

    def test_missing_usage_does_not_become_zero(self):
        pending = self.ledger.reserve('one', 'x', 'A')
        with self.assertRaises(ValueError):
            self.ledger.settle('one', None)
        self.assertEqual(self.ledger.totals(), (Decimal('0'), pending))

    def test_error_rate_strictly_more_than_10_percent(self):
        for index in range(9):
            self.ledger.outcome(str(index), 'A', False)
        self.ledger.outcome('tenth', 'A', True)
        with self.assertRaisesRegex(common.StopRun, 'INTERFACE_ERROR_RATE'):
            self.ledger.outcome('eleventh', 'A', True)

    def test_run_without_paid_flag_exits_before_credentials(self):
        env = dict(os.environ)
        env.pop('VS_ALLOW_PAID', None)
        process = subprocess.run([sys.executable, '-I', '-S', '-B', str(common.TASK / 'run_compare.py'), '--run'],
                                 env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(process.returncode, 2)
        self.assertIn('PAID_DISABLED', process.stderr)
        self.assertNotIn('已读取', process.stdout)

    def test_shared_client_payload_2048_and_raw_capture(self):
        sent = []
        def transport(payload, key):
            sent.append(payload)
            return response()
        with patch.dict(os.environ, {'VS_ALLOW_PAID': '1'}):
            path, record = self.client(transport).send('B1-001', 'B', 1, '完整提示', 10)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0], runner.payload_for('完整提示'))
        self.assertEqual((sent[0]['max_tokens'], sent[0]['temperature']), (2048, 0))
        self.assertEqual(sent[0]['thinking'], {'type': 'disabled'})
        self.assertEqual(common.read_json(path)['upstream_max_new_tokens'], 10)
        self.assertEqual(record['response_text'], response()[1].decode('utf-8'))
        self.assertEqual(record['model_text'], 'Entailment')
        self.assertEqual(self.ledger.totals(), (Decimal('0.001572'), Decimal('0')))

    def test_transport_error_no_retry_and_keep_reserve(self):
        calls = []
        def fail(payload, key):
            calls.append(1)
            raise OSError('must not log ' + key)
        with patch.dict(os.environ, {'VS_ALLOW_PAID': '1'}):
            path, record = self.client(fail).send('A1-001', 'A', 1, 'x')
        self.assertEqual(calls, [1])
        self.assertTrue(record['interface_error'])
        self.assertIsNone(record['actual_estimated_cny'])
        self.assertNotIn('T1_FAKE_SECRET_ONLY', path.read_text(encoding='utf-8'))
        self.assertEqual(self.ledger.totals()[1], common.reservation('x'))

    def test_secret_echo_stops_and_is_not_written(self):
        with patch.dict(os.environ, {'VS_ALLOW_PAID': '1'}):
            with self.assertRaisesRegex(common.StopRun, 'SECRET_ECHO'):
                self.client(lambda payload, key: response(key)).send('B1-001', 'B', 1, 'x')
        self.assertNotIn('T1_FAKE_SECRET_ONLY', (self.temp / 'raw/B1-001.json').read_text(encoding='utf-8'))

    def test_json_escaped_secret_echo_is_redacted_before_saving(self):
        def transport(payload, key):
            encoded = ''.join('\\u%04x' % ord(char) for char in key)
            return 200, ('{"error":"' + encoded + '"}').encode('ascii')
        with patch.dict(os.environ, {'VS_ALLOW_PAID': '1'}):
            with self.assertRaisesRegex(common.StopRun, 'SECRET_ECHO'):
                self.client(transport).send('A1-001', 'A', 1, 'x')
        record = common.read_json(self.temp / 'raw/A1-001.json')
        self.assertIsNone(record['response'])
        self.assertEqual(record['response_text'], '[REDACTED: secret echo]')

    def test_non_json_thinking_response_stops(self):
        with patch.dict(os.environ, {'VS_ALLOW_PAID': '1'}):
            with self.assertRaisesRegex(common.StopRun, 'THINKING_CONTENT'):
                self.client(lambda payload, key: (200, b'<think>synthetic</think>')).send('A1-001', 'A', 1, 'x')

    def test_thinking_fields_and_tags_but_not_required_nli_reason(self):
        for obj in ({'reasoning_content': 'x'}, {'choices': [{'message': {'thinking': 'x'}}]}, '<think>x</think>', '<analysis>x</analysis>'):
            self.assertTrue(common.thinking_detected(obj))
        self.assertFalse(common.thinking_detected({'reasoning_content': '', 'reason': 'NLI justification', 'content': 'Neutral'}))

    def test_client_stops_on_thinking_after_saving_response(self):
        with patch.dict(os.environ, {'VS_ALLOW_PAID': '1'}):
            with self.assertRaisesRegex(common.StopRun, 'THINKING_CONTENT'):
                self.client(lambda payload, key: response(reasoning_content='synthetic thought')).send('A1-001', 'A', 1, 'x')
        record = common.read_json(self.temp / 'raw/A1-001.json')
        self.assertEqual(record['status'], 'STOPPED_THINKING_CONTENT')
        self.assertIsNone(record['native_parser'])

    def test_empty_response_is_interface_error(self):
        with patch.dict(os.environ, {'VS_ALLOW_PAID': '1'}):
            _, record = self.client(lambda payload, key: response('')).send('A1-001', 'A', 1, 'x')
        self.assertTrue(record['interface_error'])
        self.assertEqual(record['model_text'], '')

    def test_analysis_exact_advantage_rule(self):
        cases = [({'A': [46, 46, 46], 'B': [45, 45, 45]}, 'A'),
                 ({'A': [45, 45, 45], 'B': [46, 46, 46]}, 'B'),
                 ({'A': [46, 46, 44], 'B': [44, 44, 44]}, None),
                 ({'A': [44, 44, 44], 'B': [43, 46, 46]}, None),
                 ({}, None)]
        for scores, winner in cases:
            with self.subTest(scores=scores):
                result = analyze.analyze_rows(synthetic_rows(self.inputs, scores), self.inputs)
                self.assertEqual(result['winner'], winner)
                self.assertEqual(result['request_count'], 216)
                self.assertEqual(result['usage_total']['prompt_tokens'], 2160)

    def test_analysis_no_result_preserves_denominator_and_is_not_binary_flip(self):
        rows = synthetic_rows(self.inputs)
        for row in rows:
            if (row['arm'], row['round'], row['statement_id']) == ('A', 1, 'S01'):
                row.update(judgment='无结果', original_label=None, parse_failed=True)
            if (row['arm'], row['round'], row['statement_id']) == ('B', 1, 'S01'):
                row.update(judgment='不支持', original_label='Neutral')
        result = analyze.analyze_rows(rows, self.inputs)
        self.assertEqual(result['arms']['A']['runs'][0]['agree'], 45)
        self.assertEqual(result['arms']['A']['runs'][0]['denominator'], 46)
        self.assertEqual(result['arms']['A']['binary_flip_count'], 0)
        self.assertEqual(result['arms']['A']['any_no_result_count'], 1)
        self.assertEqual(result['arms']['A']['all_state_change_count'], 1)
        self.assertEqual(result['arms']['B']['binary_flip_count'], 1)
        self.assertEqual(set(result['arms']['A']['unresolved']), common.UNRESOLVED)

    def test_incomplete_duplicate_or_failed_supported_results_refused(self):
        rows = synthetic_rows(self.inputs)
        for bad in (rows[:-1], rows + [rows[0]]):
            with self.assertRaises(common.StopRun):
                analyze.analyze_rows(bad, self.inputs)
        rows[0]['parse_failed'] = True
        with self.assertRaisesRegex(common.StopRun, 'PARSE_FAILURE_MUST_BE_NO_RESULT'):
            analyze.analyze_rows(rows, self.inputs)

    def test_outside_task_write_rejected(self):
        with self.assertRaisesRegex(common.StopRun, 'OUT_OF_SCOPE_WRITE'):
            common.write_json(common.ROOT / 'forbidden-T1.json', {})


class PromptChecks:
    def check_fake_execution(self, arm, loaded):
        output = Path(tempfile.mkdtemp(prefix=f'fake-full-{arm}-', dir=common.TASK / 'T1-tmp'))
        ledger = common.BudgetLedger(output / 'budget-ledger.jsonl')
        sent = []

        def transport(payload, key):
            sent.append(payload)
            if arm == 'B':
                return response('Entailment')
            actual = payload['messages'][0]['content'].rsplit('Your actual task:\n\n', 1)[1]
            statements_line = actual.split('\n', 2)[1].removeprefix('statements: ')
            statements = json.loads(json.loads(statements_line))
            return response(json.dumps([{'statement': s, 'reason': 'synthetic fake LLM', 'verdict': 1} for s in statements]))

        client = runner.DeepSeekClient(ledger, output / 'raw', transport=transport, key='T1_FAKE_SECRET_ONLY')
        with patch.dict(os.environ, {'VS_ALLOW_PAID': '1'}), redirect_stderr(io.StringIO()):
            runner.execute_arm(arm, 1, common.question_groups(common.load_inputs()[0]), loaded, output, client)
        rows = common.read_jsonl(output / 'results.jsonl')
        self.assertEqual(len(rows), 52)
        self.assertEqual({row['statement_id'] for row in rows}, {f'S{i:02}' for i in range(1, 53)})
        self.assertTrue(all(row['judgment'] == '支持' and not row['parse_failed'] for row in rows))
        self.assertEqual(len(sent), 20 if arm == 'A' else 52)
        self.assertEqual(len(list((output / 'raw').glob('*.json'))), len(sent))
        self.assertEqual(ledger.totals(), (Decimal('0.001572') * len(sent), Decimal('0')))
        for path in (output / 'raw').glob('*.json'):
            raw = common.read_json(path)
            self.assertIsNotNone(raw['native_parser']['return'])
            self.assertIsNotNone(raw['outer_results'])
            self.assertEqual(raw['response']['model'], 'synthetic-model')
        print(f'SYNTHETIC_ONLY {arm} fake_requests={len(sent)} result_rows=52 artifacts={output.relative_to(common.TASK)}')

    def check_prompts(self, arm, loaded):
        fixture = common.load_inputs()[0]
        poisoned = deepcopy(fixture)
        marker = 'DO_NOT_SEND_HUMAN_METADATA_20261005'
        for field in ('source', 'guide', 'labels', 'rules', 'annotator', 'notAuthor', 'savedAt'):
            poisoned[field] = marker
        for obj in poisoned['questions'] + poisoned['items']:
            obj.update(caseId='F99', originalQuestionId='D99', judge=marker, human_label=marker,
                       annotator=marker, notAuthor=marker)
        with redirect_stderr(io.StringIO()):
            plan = runner.prepared_requests(arm, common.question_groups(fixture), loaded)
            poisoned_plan = runner.prepared_requests(arm, common.question_groups(poisoned), loaded)
        self.assertEqual(plan, poisoned_plan)
        for row in plan:
            prompt = row['payload']['messages'][0]['content']
            self.assertNotIn(marker, prompt)
            self.assertIsNone(re.search(r'\b[QCFD]\d{2}\b', prompt))
            self.assertNotIn('20261005T144504Z', prompt)
            self.assertNotIn('notAuthor', prompt)
            self.assertNotIn('annotator', prompt)
        return plan


class ATests(PromptChecks, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = runner.load_a()

    def test_original_parser_mapping_and_no_repair_call(self):
        raw = json.dumps([{'statement': '甲', 'reason': 'fake', 'verdict': 1},
                          {'statement': '乙', 'reason': 'fake', 'verdict': 0}])
        native, outer = runner.parse_a(self.loaded[1], raw, 2)
        self.assertEqual([row['verdict'] for row in native['return']], [1, 0])
        self.assertEqual([row['judgment'] for row in outer], ['支持', '不支持'])
        self.assertFalse(any(row['parse_failed'] for row in outer))

    def test_fake_full_arm_execution(self):
        self.check_fake_execution('A', self.loaded)

    def test_parser_exception_missing_verdict_and_count_mismatch(self):
        values = ['not JSON', '', '[{"statement":"x","reason":"fake"}]',
                  '[{"statement":"x","reason":"fake","verdict":1}]']
        for raw in values:
            native, outer = runner.parse_a(self.loaded[1], raw, 2)
            self.assertEqual([row['judgment'] for row in outer], ['无结果', '无结果'])
            self.assertTrue(all(row['parse_failed'] for row in outer))
        self.assertEqual(native['return'][0]['verdict'], 1)

    def test_prompt_full_statements_contexts_and_no_metadata(self):
        plan = self.check_prompts('A', self.loaded)
        self.assertEqual(len(plan), 20)
        self.assertEqual(sum(len(row['statement_ids']) for row in plan), 52)
        for group, request in zip(common.question_groups(common.load_inputs()[0]), plan):
            original = self.loaded[0]._create_nli_prompt({'contexts': group['contexts']}, group['statements']).to_string()
            self.assertEqual(request['prompt'], original)
            actual = original.rsplit('Your actual task:\n\n', 1)[1]
            context_line, statements_line, _ = actual.split('\n', 2)
            self.assertEqual(json.loads(context_line.removeprefix('context: ')), '\n'.join(group['contexts']))
            # Ragas serializes the already-JSON statement string again. Preserve it.
            self.assertEqual(json.loads(json.loads(statements_line.removeprefix('statements: '))), group['statements'])


class BTests(PromptChecks, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = runner.load_b()

    def call(self, text):
        with redirect_stderr(io.StringIO()):
            return runner.call_b(*self.loaded, '完整陈述', '原文', '题面', lambda prompt, maximum: text)

    def test_original_mapping_case_and_contradiction_priority(self):
        for text, label, judgment in [('ENTAILMENT', 'Entailment', '支持'), ('Neutral', 'Neutral', '不支持'),
                                      ('contradiction', 'Contradiction', '不支持'),
                                      ('Entailment and Contradiction', 'Contradiction', '不支持')]:
            _, native, outer = self.call(text)
            self.assertEqual(native['return'], [[[label]]])
            self.assertEqual(outer[0]['judgment'], judgment)
            self.assertFalse(outer[0]['parse_failed'])

    def test_fake_full_arm_execution(self):
        self.check_fake_execution('B', self.loaded)

    def test_native_neutral_without_label_is_outer_no_result(self):
        _, native, outer = self.call('unparseable synthetic answer')
        self.assertEqual(native['return'], [[['Neutral']]])
        self.assertEqual(outer[0], {'judgment': '无结果', 'original_label': 'Neutral', 'parse_failed': True})

    def test_empty_response_keeps_original_exception(self):
        _, native, outer = self.call('')
        self.assertIsNone(native['return'])
        self.assertEqual(native['exception']['type'], 'TypeError')
        self.assertEqual(outer[0]['judgment'], '无结果')

    def test_native_prompt_exact_full_sentence_question_and_10_token_override(self):
        captured, _, _ = self.call('Neutral')
        expected = self.loaded[1].prompt_temp_subsent.format(reference='题面 原文', claim='完整陈述')
        self.assertEqual(captured['prompt'], expected)
        self.assertEqual(captured['upstream_max_new_tokens'], 10)
        self.assertEqual(runner.payload_for(captured['prompt'])['max_tokens'], 2048)

    def test_all_52_prompts_without_metadata(self):
        plan = self.check_prompts('B', self.loaded)
        self.assertEqual(len(plan), 52)
        self.assertTrue(all(len(row['statement_ids']) == 1 for row in plan))
        items = common.load_inputs()[0]['items']
        self.assertTrue(all(item['statement'] in request['prompt'] for item, request in zip(items, plan)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--arm', choices=('A', 'B'), required=True)
    args = parser.parse_args()
    common.configure(offline=True)
    suite = unittest.TestSuite()
    for case in (CommonTests, ATests if args.arm == 'A' else BTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
