import { beforeEach, describe, expect, it, vi } from 'vitest';
import { importInput, MAX_INPUT_BYTES, validateInputFile } from '../api/inputFiles';

let requests: ReturnType<typeof vi.fn>;
beforeEach(() => { requests = vi.fn(); vi.stubGlobal('fetch', requests); });

describe('File import boundary validation', () => {
  it.each([
    [new File([], '资料.txt'), '文件为空'],
    [new File(['abc'], '资料.exe'), '请选择 TXT'],
    [new File(['abc'], '../资料.txt'), '文件名不合法'],
    [new File(['abc'], 'a\\资料.txt'), '文件名不合法'],
    [new File(['abc'], `${'文'.repeat(90)}.txt`), '文件名不合法'],
    [new File([new Uint8Array(MAX_INPUT_BYTES + 1)], '资料.txt'), '超过 10MB'],
  ])('rejects an invalid document before sending its contents: %s', async (file, message) => {
    await expect(importInput(file as File, 'document')).rejects.toThrow(message as string);
    expect(requests).not.toHaveBeenCalled();
  });

  it('uses different allowed formats for documents and testsets', () => {
    expect(validateInputFile(new File(['{}'], '题集.JSON'), 'testset')).toBeNull();
    expect(validateInputFile(new File(['x'], '题集.pdf'), 'testset')).toMatch('请选择 JSON');
    expect(validateInputFile(new File(['x'], '资料.DOCX'), 'document')).toBeNull();
  });

  it.each([
    { kind: 'testset' },
    { file_name: 'other.txt' },
    { size_bytes: 99 },
    { relative_path: '../../demo.txt' },
    { relative_path: `work/uploads/${'a'.repeat(32)}.exe` },
  ])('rejects a mismatched or unsafe successful response: %j', async (changed) => {
    const file = new File(['abc'], '资料.txt');
    requests.mockResolvedValueOnce(new Response(JSON.stringify({ kind: 'document', file_name: file.name,
      size_bytes: file.size, relative_path: `work/uploads/${'a'.repeat(32)}.txt`, ...changed }), { status: 200 }));
    await expect(importInput(file, 'document')).rejects.toThrow('尚未替换已选资料');
    expect(requests).toHaveBeenCalledTimes(1);
  });

  it('keeps backend error bodies out of user-facing messages and does not retry', async () => {
    requests.mockResolvedValueOnce(new Response('private backend details', { status: 423 }));
    await expect(importInput(new File(['abc'], '资料.txt'), 'document')).rejects.toThrow('公开演示不接收文件');
    expect(requests).toHaveBeenCalledTimes(1);
  });

  it('reports unreadable successful JSON as a failed import', async () => {
    requests.mockResolvedValueOnce(new Response('not JSON', { status: 200 }));
    await expect(importInput(new File(['abc'], '资料.txt'), 'document')).rejects.toThrow('导入结果无法读取');
  });
});
