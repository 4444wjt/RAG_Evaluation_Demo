'use strict';
// Read-only audit of archived bytes and arithmetic. Standard Node.js modules only.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');

const root = __dirname;
const read = name => fs.readFileSync(path.join(root, name));
const json = name => JSON.parse(read(name).toString('utf8'));
const jsonl = name => read(name).toString('utf8').trim().split('\n').map(JSON.parse);
const hash = name => crypto.createHash('sha256').update(read(name)).digest('hex');
const near = (actual, expected) => {
  assert.equal(typeof actual, 'number');
  assert.equal(typeof expected, 'number');
  assert.ok(Number.isFinite(actual) && Number.isFinite(expected));
  assert.ok(Math.abs(actual - expected) < 1e-12, `${actual} != ${expected}`);
};
const units = (value, places = 8) => {
  const [whole, fraction = ''] = String(value).split('.');
  assert.match(whole, /^\d+$/);
  assert.ok(fraction.length <= places);
  return BigInt(whole) * 10n ** BigInt(places) + BigInt(fraction.padEnd(places, '0'));
};
const files = (dir = '') => fs.readdirSync(path.join(root, dir), { withFileTypes: true })
  .flatMap(entry => {
    const name = dir ? `${dir}/${entry.name}` : entry.name;
    return entry.isDirectory() ? files(name) : [name];
  }).sort();
const parsedContent = response => {
  let content = response.choices[0].message.content.trim();
  if (content.startsWith('```')) {
    assert.ok(content.endsWith('```'));
    content = content.slice(3, -3).replace(/^json\s*/, '').trim();
  }
  const parsed = JSON.parse(content);
  assert.ok(Array.isArray(parsed));
  return parsed;
};

const audit = json('archive-audit.json');
for (const file of audit.files) {
  assert.equal(hash(file.path), file.archive_sha256);
  assert.equal(file.archive_sha256, file.source_sha256);
}
assert.equal(audit.files.length, audit.byte_identical_files);
const sums = read('SHA256SUMS.txt').toString('utf8');
assert.ok(!sums.includes('\r') && sums.endsWith('\n'));
const listed = sums.trimEnd().split('\n').map(line => {
  const match = /^([a-f0-9]{64})  (.+)$/.exec(line);
  assert.ok(match, 'Invalid SHA256SUMS line');
  assert.equal(hash(match[2]), match[1], match[2]);
  return match[2];
});
assert.deepEqual(listed, files().filter(name => name !== 'SHA256SUMS.txt'));

const freeze = json('freeze.json');
for (const [name, expected] of Object.entries(freeze.frozen_files)) assert.equal(hash(name), expected);
const results = jsonl('results.jsonl');
const ledger = jsonl('budget-ledger.jsonl');
const summary = json('summary.json');
const analysis = json('analysis.json');
const arithmetic = json('arithmetic-audit.json');
const pricing = json('pricing.json');
const inputById = new Map(jsonl('inputs.jsonl').map(row => [row.id, row]));
const schedule = json('schedule.json');
assert.deepEqual(results.map(r => r.combination_id), schedule.map(r => r.combination_id));
assert.equal(new Set(results.map(r => r.combination_id)).size, results.length);
assert.deepEqual(freeze.sample_ids, analysis.frozen_sample_ids);
assert.equal(results.length, summary.attempted_combinations);
assert.equal(results.length, analysis.arithmetic_scored);
assert.equal(results.length, arithmetic.length);
const textChanged = [], countChanged = [];
for (const result of results) {
  assert.equal(result.input_sha256, inputById.get(result.case_id).input_sha256);
  const original = arithmetic.find(item => item.combination_id === result.combination_id);
  const responses = result.request_ids.map(id => parsedContent(json(`requests/${id}.response.json`)));
  assert.equal(responses.length, 2);
  const extraction = responses[0].flatMap(item => item.simpler_statements);
  const nli = responses[1];
  assert.ok(extraction.every(statement => typeof statement === 'string'));
  assert.ok(nli.length > 0 && nli.every(item => item.verdict === 0 || item.verdict === 1));
  const score = nli.filter(item => Boolean(item.verdict)).length / nli.length;
  near(score, result.score);
  near(score, original.independently_recomputed_score);
  assert.equal(original.arithmetic_matches, true);
  assert.deepEqual(extraction, original.extraction_statement_texts);
  assert.deepEqual(nli.map(item => item.statement), original.nli_statement_texts);
  assert.equal(extraction.length, original.extracted_statements);
  assert.equal(nli.length, original.judged_statements);
  assert.equal(nli.filter(item => Boolean(item.verdict)).length, original.supported_statements);
  if (JSON.stringify(extraction) !== JSON.stringify(nli.map(item => item.statement))) textChanged.push(result.combination_id);
  if (extraction.length !== nli.length) countChanged.push(result.combination_id);
}
assert.deepEqual(textChanged, analysis.nli_text_changed_combinations);
assert.deepEqual(countChanged, analysis.nli_count_changed_combinations);
assert.deepEqual(analysis.arithmetic_mismatches, []);

const reserved = ledger.filter(e => e.event === 'reserved');
const received = ledger.filter(e => e.event === 'response_received');
const settled = ledger.filter(e => e.event === 'settled');
assert.equal(reserved.length, summary.reserved_requests);
assert.equal(received.length, summary.completed_http_responses);
assert.equal(settled.length, reserved.length);
for (const events of [reserved, received, settled]) assert.equal(new Set(events.map(e => e.request_id)).size, events.length);
assert.deepEqual(reserved.map(e => e.request_id).sort(), results.flatMap(r => r.request_ids).sort());
assert.deepEqual(received.map(e => e.request_id).sort(), reserved.map(e => e.request_id).sort());
assert.deepEqual(settled.map(e => e.request_id).sort(), reserved.map(e => e.request_id).sort());
let totalCost = 0n, promptTokens = 0, completionTokens = 0;
for (const event of reserved) {
  assert.equal(hash(`requests/${event.request_file}`), event.request_sha256);
  const responseEvent = received.find(e => e.request_id === event.request_id);
  const settlement = settled.find(e => e.request_id === event.request_id);
  assert.equal(hash(`requests/${responseEvent.response_file}`), responseEvent.response_sha256);
  const response = json(`requests/${responseEvent.response_file}`);
  assert.deepEqual(settlement.usage, response.usage);
  assert.deepEqual(responseEvent.usage, response.usage);
  const model = pricing.models.find(m => m.id === event.judge_id);
  const { prompt_tokens: p, completion_tokens: c, total_tokens: t } = response.usage;
  assert.ok([p, c, t].every(v => Number.isInteger(v) && v >= 0));
  assert.equal(p + c, t);
  const cost = BigInt(p) * units(model.input_cny_m, 2) + BigInt(c) * units(model.output_cny_m, 2);
  assert.equal(cost, units(settlement.estimated_ceiling_cost_cny));
  assert.equal(settlement.actual_bill_cny, null);
  totalCost += cost; promptTokens += p; completionTokens += c;
}
assert.equal(totalCost, units(summary.estimated_ceiling_cost_cny));
assert.equal(promptTokens, analysis.tokens.prompt_tokens);
assert.equal(completionTokens, analysis.tokens.completion_tokens);
assert.equal(analysis.tokens.unknown_usage_requests, 0);
const modelResults = {};
for (const [id, model] of Object.entries(summary.model_results)) {
  const means = [1, 2].map(repeat => {
    const rows = results.filter(r => r.judge_id === id && r.repeat === repeat);
    assert.equal(rows.length, freeze.sample_ids.length);
    const mean = rows.reduce((sum, r) => sum + r.score, 0) / rows.length;
    near(mean, model.repetitions[String(repeat)].all_ten_mean);
    assert.equal(model.repetitions[String(repeat)].exploratory_subset_gate_at_0_8, mean >= 0.8 ? 'PASS' : 'FAIL');
    return mean;
  });
  for (const pair of model.paired_cases) {
    const scores = results.filter(r => r.judge_id === id && r.case_id === pair.case_id).map(r => r.score);
    assert.equal(scores.length, 2);
    near(Math.abs(scores[0] - scores[1]), pair.absolute_difference);
  }
  const cost = settled.filter(e => e.request_id.includes(`-${id}-`)).reduce((sum, e) => sum + units(e.estimated_ceiling_cost_cny), 0n);
  assert.equal(cost, units(model.estimated_ceiling_cost_cny));
  modelResults[id] = { means, changed_cases: model.paired_cases.filter(p => p.absolute_difference > 0).length, estimated_ceiling_cost_cny: model.estimated_ceiling_cost_cny };
}
for (const item of analysis.unweighted_ensemble_descriptive) {
  const rows = results.filter(r => r.repeat === item.repeat);
  near(rows.reduce((sum, r) => sum + r.score, 0) / rows.length, item.mean);
}
const durations = received.map(e => e.elapsed_seconds).sort((a, b) => a - b);
near((durations[59] + durations[60]) / 2, analysis.latency.p50_seconds);
near(durations[Math.ceil(0.95 * durations.length) - 1], analysis.latency.p95_nearest_rank_seconds);
assert.equal(summary.git_status_unchanged, false);
assert.equal(summary.actual_paid_bill_cny, null);
console.log(JSON.stringify({ status: 'OK', hashes_checked: listed.length, byte_identical_copies: audit.files.length, scores_recomputed: results.length, requests_verified: reserved.length, text_changed_combinations: textChanged.length, count_changed_combinations: countChanged.length, prompt_tokens: promptTokens, completion_tokens: completionTokens, estimated_ceiling_cost_cny: summary.estimated_ceiling_cost_cny, model_results: modelResults }, null, 2));
