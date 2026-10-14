import { READ_ONLY, STATIC_SITE } from '../config';

export type InputKind = 'document' | 'testset';
export interface ImportedInput {
  kind: InputKind;
  file_name: string;
  relative_path: string;
  size_bytes: number;
}

export const MAX_INPUT_BYTES = 10 * 1024 * 1024;
export const INPUT_EXTENSIONS = {
  document: ['txt', 'pdf', 'docx', 'md'],
  testset: ['json', 'csv', 'txt'],
} as const;

export function validateInputFile(file: File, kind: InputKind): string | null {
  if (!file.size) return '文件为空，请选择包含内容的文件。';
  if (file.size > MAX_INPUT_BYTES) return '文件超过 10MB，请选择较小的文件。';
  if (!file.name || file.name !== file.name.trim() || /[\/\\:<>|"?*\p{C}]/u.test(file.name) || new TextEncoder().encode(file.name).length > 255) return '文件名不合法，请更换文件名后重试。';
  const extension = file.name.split('.').pop()?.toLowerCase();
  if (!extension || !(INPUT_EXTENSIONS[kind] as readonly string[]).includes(extension)) {
    return kind === 'document' ? '请选择 TXT、PDF、DOCX 或 MD 资料。' : '请选择 JSON、CSV 或 TXT 测试集。';
  }
  return null;
}

export async function importInput(file: File, kind: InputKind, signal?: AbortSignal): Promise<ImportedInput> {
  if (READ_ONLY || STATIC_SITE) throw new Error('公开演示不接收文件，请在本地运行后导入。');
  const invalid = validateInputFile(file, kind);
  if (invalid) throw new Error(invalid);
  let response: Response;
  try {
    response = await fetch(`/api/inputs?${new URLSearchParams({ kind, filename: file.name })}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file, signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error('无法连接本地导入服务，请确认后端已启动后重试。');
  }
  if (!response.ok) {
    const messages: Record<number, string> = {
      403: '本地资料目录无法访问，请检查后端目录权限。',
      404: '当前后端未提供文件导入，请启动最新版本的本地服务。',
      413: '文件超过 10MB，请选择较小的文件。',
      422: '文件未被接收，请检查文件名、格式和内容。',
      423: '公开演示不接收文件，请在本地运行后导入。',
    };
    throw new Error(messages[response.status] ?? '资料导入未完成，请检查本地后端后重试。');
  }
  let value: unknown;
  try { value = await response.json(); } catch { throw new Error('导入结果无法读取，请检查本地后端。'); }
  if (typeof value !== 'object' || value === null) throw new Error('导入结果不完整，尚未替换已选资料。');
  const result = value as Record<string, unknown>;
  const path = result.relative_path;
  const suffix = typeof path === 'string' ? path.split('.').pop() : undefined;
  if (result.kind !== kind || result.file_name !== file.name || result.size_bytes !== file.size
    || typeof path !== 'string' || !/^work\/uploads\/[0-9a-f]{32}\.[a-z]+$/.test(path)
    || !suffix || !(INPUT_EXTENSIONS[kind] as readonly string[]).includes(suffix)) {
    throw new Error('导入结果不完整，尚未替换已选资料。');
  }
  return result as unknown as ImportedInput;
}
