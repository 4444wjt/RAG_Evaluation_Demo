import type { ImportedInput } from '../api/inputFiles';
import type { RecognizedDocument, RecognitionKind, SourceLocations } from '../api/recognition';

export const uploadId = 'a'.repeat(32);
export const recordId = 'c'.repeat(32);
export const recognitionPath = `work/uploads/${uploadId}.recognition.json`;
export const textPath = `work/uploads/${uploadId}.txt`;
export const sourceSha = 'd'.repeat(64);
export const textSha = 'e'.repeat(64);
export const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
export const original = (kind: RecognitionKind = 'ocr'): ImportedInput => ({
  kind: kind === 'ocr' ? 'image' : 'audio', file_name: kind === 'ocr' ? '法规.png' : '法规.wav',
  relative_path: `work/uploads/${uploadId}.${kind === 'ocr' ? 'png' : 'wav'}`, size_bytes: 4,
});
export const successful = (kind: RecognitionKind = 'ocr') => ({ status: 'ok', kind, text_path: textPath, recognition_path: recognitionPath });

export function recognized(kind: RecognitionKind = 'ocr'): RecognizedDocument {
  return {
    text: kind === 'ocr' ? '😀未经批准\n未经批准' : '第一段\n第二段',
    sidecar: {
      schema: 'veriscope.recognition-sidecar.v1', elapsed_ms: 25,
      source: { relative_path: original(kind).relative_path, sha256: sourceSha },
      text: { relative_path: textPath, sha256: textSha },
      spans: kind === 'ocr' ? [
        { start: 0, end: 5, page: 1, line_id: 'p1-l0', box: [[0, 0], [100, 0], [100, 20], [0, 20]], conf: 0.63, char_conf: null },
        { start: 6, end: 10, page: 1, line_id: 'p1-l1', box: [[0, 30], [100, 30], [100, 50], [0, 50]], conf: null, char_conf: null },
      ] : [
        { start: 0, end: 3, segment_id: 's0', start_ms: 1250, end_ms: 2000, conf: null, char_conf: null },
        { start: 4, end: 7, segment_id: 's1', start_ms: 3000, end_ms: 4000, conf: 0.91, char_conf: null },
      ],
      recognition: {
        kind, engine: { name: 'constructed-test-plugin', version: '1', backend: 'test' },
        input: { width: kind === 'ocr' ? 200 : null, height: kind === 'ocr' ? 100 : null, duration_ms: kind === 'asr' ? 5000 : null }, warnings: [],
      },
    },
  };
}

export function located(): SourceLocations {
  const refs = recognized().sidecar.spans;
  return {
    schema: 'veriscope.source-locations.v1', parent_record_id: recordId, recognition_path: recognitionPath,
    source: { relative_path: original().relative_path, sha256: sourceSha }, text: { relative_path: textPath, sha256: textSha },
    models: { '构造测试模型': [
      { row_index: 0, trace_status: 'available', statements: [{ text: '待核对陈述', evidence: '未经批准', verdict: 'supported', source_locations: {
        status: 'ambiguous', matches: [
          { start: 1, end: 5, refs: [refs[0]], min_conf: 0.63, conf_missing: false },
          { start: 6, end: 10, refs: [refs[1]], min_conf: null, conf_missing: true },
        ],
      } }] },
      { row_index: 1, trace_status: 'missing', statements: null },
      { row_index: 2, trace_status: 'invalid', statements: null },
      { row_index: 3, trace_status: 'available', statements: [] },
      { row_index: 4, trace_status: 'available', statements: [
        { text: '无匹配', evidence: '不存在', source_locations: { status: 'not_found', matches: [] } },
        { text: '空引文', evidence: '', source_locations: { status: 'empty_quote', matches: [] } },
        { text: '坏引文', evidence: 3, source_locations: { status: 'invalid_quote', matches: [] } },
      ] },
    ] },
  };
}
