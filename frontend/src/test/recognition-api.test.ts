import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchRecognition, fetchRecognitionMedia, fetchSourceLocations, RECOGNITION_MESSAGES, recognizeInput } from '../api/recognition';
import { importInput, MAX_INPUT_BYTES, validateInputFile } from '../api/inputFiles';
import { fromRequest, initialConfig, toRequest } from '../state';
import { located, original, recognized, recognitionPath, recordId, response, sourceSha, successful, textPath } from './recognitionFixtures';

const mode = vi.hoisted(() => ({ BYOK: false, READ_ONLY: false, STATIC_SITE: false }));
vi.mock('../config', async (originalModule) => ({ ...await originalModule<typeof import('../config')>(),
  get BYOK() { return mode.BYOK; }, get READ_ONLY() { return mode.READ_ONLY; }, get STATIC_SITE() { return mode.STATIC_SITE; },
}));
let requests: ReturnType<typeof vi.fn>;
beforeEach(() => { Object.assign(mode, { BYOK: false, READ_ONLY: false, STATIC_SITE: false }); requests = vi.fn(); vi.stubGlobal('fetch', requests); });

describe('本机识别接口边界', () => {
  it.each(Object.keys(RECOGNITION_MESSAGES))('保留识别状态 %s，不重试或补造成功', async (status) => {
    requests.mockResolvedValueOnce(response({ ...successful(), status }));
    const result = await recognizeInput('ocr', original().relative_path);
    expect(result.status).toBe(status); expect(requests).toHaveBeenCalledTimes(1);
    expect(requests.mock.calls[0]).toEqual(['/api/recognitions', expect.objectContaining({ method: 'POST', cache: 'no-store', body: JSON.stringify({ kind: 'ocr', relative_path: original().relative_path }) })]);
  });

  it.each(['image', 'audio'] as const)('扩展 %s 导入且保留 10MB 和原字节约束', async (kind) => {
    const file = new File(['abcd'], kind === 'image' ? '法规.PNG' : '法规.FLAC');
    expect(validateInputFile(file, kind)).toBeNull();
    expect(validateInputFile(new File([new Uint8Array(MAX_INPUT_BYTES + 1)], file.name), kind)).toContain('10MB');
    const path = original(kind === 'image' ? 'ocr' : 'asr').relative_path.replace(/\.[^.]+$/, kind === 'image' ? '.png' : '.flac');
    requests.mockResolvedValueOnce(response({ kind, file_name: file.name, size_bytes: file.size, relative_path: path }));
    await expect(importInput(file, kind)).resolves.toMatchObject({ kind, relative_path: path });
    expect(requests.mock.calls[0][1].body).toBe(file);
  });

  it('拒绝未开放的音频后缀，识别元数据不进入评测请求', () => {
    expect(validateInputFile(new File(['a'], 'a.mp3'), 'audio')).toContain('16kHz');
    const request = toRequest({ ...initialConfig, docPath: textPath, recognitionPath, recognitionSource: original() });
    expect(request.doc_path).toBe(textPath); expect(request).not.toHaveProperty('recognitionPath'); expect(request).not.toHaveProperty('recognitionSource');
  });

  it('重开同路径历史仅保留本会话已验证的原件，不改变历史评测参数', () => {
    const current = { ...initialConfig, docPath: textPath, recognitionPath, recognitionSource: original(),
      docImport: { kind: 'document' as const, relative_path: textPath, file_name: '法规.txt', size_bytes: 30 } };
    const savedRequest = { ...toRequest(current), model_names: ['历史模型'], faith_threshold: 0.7, ctx_prec_threshold: 0.9 };
    const restored = fromRequest(savedRequest, current);
    expect(restored.recognitionPath).toBe(recognitionPath); expect(restored.recognitionSource).toEqual(original()); expect(restored.docImport).toEqual(current.docImport);
    expect(toRequest(restored)).toEqual(savedRequest);
  });

  it('异路径历史及新会话不推测识别元数据', () => {
    const current = { ...initialConfig, docPath: textPath, recognitionPath, recognitionSource: original() };
    const different = fromRequest(toRequest(initialConfig), current);
    expect(different.recognitionPath).toBeUndefined(); expect(different.recognitionSource).toBeUndefined(); expect(different.docImport).toBeUndefined();
    const newPage = fromRequest(toRequest(current));
    expect(newPage.docPath).toBe(textPath); expect(newPage.recognitionPath).toBeUndefined(); expect(newPage.recognitionSource).toBeUndefined();
  });

  it.each(['READ_ONLY', 'STATIC_SITE', 'BYOK'] as const)('%s 模式在联网前拒绝全部识别接口和媒体上传', async (flag) => {
    mode[flag] = true;
    await expect(recognizeInput('ocr', original().relative_path)).rejects.toThrow(/本机识别/);
    await expect(fetchRecognition(recognitionPath)).rejects.toThrow(/本机识别/);
    await expect(fetchRecognitionMedia(original().relative_path, sourceSha)).rejects.toThrow(/本机识别/);
    await expect(fetchSourceLocations(recordId, recognitionPath, true)).rejects.toThrow(/本机识别/);
    await expect(importInput(new File(['a'], 'a.png'), 'image')).rejects.toThrow();
    expect(requests).not.toHaveBeenCalled();
  });

  it('不接收另一上传 ID 的成功识别结果', async () => {
    requests.mockResolvedValueOnce(response({ ...successful(), text_path: `work/uploads/${'b'.repeat(32)}.txt` }));
    await expect(recognizeInput('ocr', original().relative_path)).rejects.toThrow('尚未采用');
  });

  it('保留完整 Unicode 文字与 null 置信度', async () => {
    requests.mockResolvedValueOnce(response(recognized()));
    const result = await fetchRecognition(recognitionPath);
    expect(result.text).toBe('😀未经批准\n未经批准'); expect(result.sidecar.spans[1].conf).toBeNull();
  });

  it.each([
    (value: ReturnType<typeof recognized>) => { value.sidecar.source.relative_path = '../../x.png'; },
    (value: ReturnType<typeof recognized>) => { value.sidecar.spans[0].conf = Number.NaN; },
    (value: ReturnType<typeof recognized>) => { value.sidecar.spans[0].end = 99; },
    (value: ReturnType<typeof recognized>) => { value.sidecar.recognition.input.width = 0; },
  ])('拒绝损坏的识别旁挂结构', async (change) => {
    const data = recognized(); change(data);
    // NaN cannot travel in JSON: use a non-JSON response double to exercise the numeric boundary.
    requests.mockResolvedValueOnce({ ok: true, json: async () => data });
    await expect(fetchRecognition(recognitionPath)).rejects.toThrow('尚未采用');
  });

  it('读取原始媒体并核对后端返回的实际字节 SHA', async () => {
    requests.mockResolvedValueOnce(new Response('original-bytes', { headers: { 'X-Content-SHA256': sourceSha } }));
    await expect(fetchRecognitionMedia(original().relative_path, sourceSha)).resolves.toBeInstanceOf(Blob);
    requests.mockResolvedValueOnce(new Response('changed-bytes', { headers: { 'X-Content-SHA256': 'f'.repeat(64) } }));
    await expect(fetchRecognitionMedia(original().relative_path, sourceSha)).rejects.toThrow('原件字节');
  });

  it('定位 POST 只提交识别路径；GET 重开保留全部题目、歧义及空值', async () => {
    requests.mockResolvedValueOnce(response(located())).mockResolvedValueOnce(response(located()));
    expect((await fetchSourceLocations(recordId, recognitionPath, true)).models['构造测试模型']).toHaveLength(5);
    expect(requests.mock.calls[0][1].body).toBe(JSON.stringify({ recognition_path: recognitionPath }));
    const result = await fetchSourceLocations(recordId, recognitionPath, false);
    expect(requests.mock.calls[1][1].method).toBeUndefined();
    expect(result.models['构造测试模型'][0].statements![0].source_locations.matches).toHaveLength(2);
    expect(result.models['构造测试模型'][1].statements).toBeNull();
  });

  it('不接收另一父记录的定位', async () => {
    requests.mockResolvedValueOnce(response({ ...located(), parent_record_id: 'b'.repeat(32) }));
    await expect(fetchSourceLocations(recordId, recognitionPath, true)).rejects.toThrow('尚未采用');
  });

  it.each([403, 404, 409, 422, 423, 500])('HTTP %i 只显示安全诊断，不自动重试', async (status) => {
    requests.mockResolvedValueOnce(response({ detail: 'private C:/secret auth=hidden' }, status));
    const error = await fetchRecognition(recognitionPath).catch((cause) => cause as Error);
    expect(error).toBeInstanceOf(Error);
    if (!(error instanceof Error)) throw new Error('接口应拒绝该响应。');
    expect(error.message).not.toMatch(/private|secret|hidden/); expect(requests).toHaveBeenCalledTimes(1);
  });
});
