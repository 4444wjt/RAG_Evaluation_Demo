import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RECOGNITION_MESSAGES } from '../api/recognition';
import type { RecognitionStatus } from '../api/recognition';
import { RecognitionPanel } from '../features/RecognitionPanel';
import type { SavedRecognitionRecord } from '../features/RecognitionPanel';
import { WorkspaceView } from '../features/WorkspaceView';
import { initialConfig, toRequest } from '../state';
import type { WorkspaceConfig } from '../state';
import { located, original, recognized, recognitionPath, recordId, response, sourceSha, successful, textPath } from './recognitionFixtures';

const mode = vi.hoisted(() => ({ BYOK: false, READ_ONLY: false, STATIC_SITE: false }));
vi.mock('../config', async (originalModule) => ({ ...await originalModule<typeof import('../config')>(),
  get BYOK() { return mode.BYOK; }, get READ_ONLY() { return mode.READ_ONLY; }, get STATIC_SITE() { return mode.STATIC_SITE; },
}));
let requests: ReturnType<typeof vi.fn>;
let createUrl: ReturnType<typeof vi.fn>;
let revokeUrl: ReturnType<typeof vi.fn>;
beforeEach(() => {
  Object.assign(mode, { BYOK: false, READ_ONLY: false, STATIC_SITE: false });
  requests = vi.fn(); vi.stubGlobal('fetch', requests);
  createUrl = vi.fn(() => 'blob:recognition-test'); revokeUrl = vi.fn();
  vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL: createUrl, revokeObjectURL: revokeUrl }));
});

function workspace(options: { config?: Partial<WorkspaceConfig>; savedRecord?: SavedRecognitionRecord; readOnly?: boolean } = {}) {
  let latest = { ...initialConfig, ...options.config };
  const run = vi.fn();
  function Harness() {
    const [config, setConfig] = useState(latest);
    return <WorkspaceView config={config} onChange={(value) => { latest = value; setConfig(value); }} onRun={run}
      running={false} hasLiveResult={false} readOnly={options.readOnly} error={null} configurationCheck={null} savedRecord={options.savedRecord}/>;
  }
  return { ...render(<Harness/>), config: () => latest, run };
}
function api(kind: 'ocr' | 'asr' = 'ocr') {
  requests.mockImplementation(async (path: string) => {
    if (path.includes('/inputs?')) return response(original(kind));
    if (path.includes('/recognitions/sidecar?')) return response(recognized(kind));
    if (path.includes('/inputs/media?')) return new Response('fake-original-bytes', { headers: { 'X-Content-SHA256': sourceSha } });
    if (path.endsWith('/source-locations')) return response(located());
    if (path === '/api/recognitions') return response(successful(kind));
    throw new Error(`unexpected test request: ${path}`);
  });
}
function retainedConfig(kind: 'ocr' | 'asr' = 'ocr'): Partial<WorkspaceConfig> {
  return { docPath: textPath, recognitionPath, recognitionSource: original(kind), docImport: { kind: 'document', relative_path: textPath, file_name: '法规.txt', size_bytes: 10 } };
}
async function uploadAndRecognize() {
  await userEvent.click(screen.getByRole('button', { name: '图片/音频识别' }));
  fireEvent.change(screen.getByLabelText('从电脑选择图片'), { target: { files: [new File(['abcd'], '法规.png')] } });
  await screen.findByText('已导入评测服务');
  await userEvent.click(screen.getByRole('button', { name: '识别为评测文字' }));
}

describe('桌面工作台识别与原件回看', () => {
  it.each(['ocr', 'asr'] as const)('新会话可恢复历史 %s 原件，不重新识别或修改评测请求', async (kind) => {
    api(kind);
    const page = workspace({ config: { docPath: textPath }, savedRecord: { id: recordId, docPath: textPath } });
    await userEvent.click(screen.getByRole('button', { name: '图片/音频识别' }));
    expect(page.config().docPath).toBe(textPath);
    await userEvent.click(screen.getByRole('button', { name: '恢复记录的识别原件' }));
    await screen.findByText(/已选用识别文字/);
    expect(page.config().recognitionSource).toMatchObject({ kind: kind === 'ocr' ? 'image' : 'audio', relative_path: original(kind).relative_path });
    expect(page.config().recognitionPath).toBe(recognitionPath);
    expect(screen.getByRole('button', { name: '定位逐句证据' })).toBeEnabled();
    expect(requests.mock.calls).toHaveLength(2);
    expect(requests.mock.calls.every(([, options]) => !options.method)).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: '图片/音频识别' }));
    expect(page.config().docPath).toBe(textPath);
    expect(screen.getByText(/已选用识别文字/)).toBeInTheDocument();
    expect(requests.mock.calls).toHaveLength(2);
  });

  it('原件不存在时恢复失败保留历史文档，可手动重试', async () => {
    requests.mockResolvedValue(response({}, 404));
    const page = workspace({ config: { docPath: textPath }, savedRecord: { id: recordId, docPath: textPath } });
    await userEvent.click(screen.getByRole('button', { name: '图片/音频识别' }));
    await userEvent.click(screen.getByRole('button', { name: '恢复记录的识别原件' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('不存在');
    expect(page.config().docPath).toBe(textPath);
    expect(page.config().recognitionSource).toBeUndefined();
    expect(createUrl).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '恢复记录的识别原件' })).toBeEnabled();
    expect(requests.mock.calls).toHaveLength(1);
  });

  it('恢复原件期间切换记录，迟到读取不会替换新资料', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const onRecognized = vi.fn();
    const props = { documentPath: textPath, disabled: false, readOnly: false, onBusy: vi.fn(), onSource: vi.fn(), onRecognized };
    const page = render(<RecognitionPanel {...props} savedRecord={{ id: recordId, docPath: textPath }}/>);
    await userEvent.click(screen.getByRole('button', { name: '恢复记录的识别原件' }));
    const signal = requests.mock.calls[0][1].signal as AbortSignal;
    page.rerender(<RecognitionPanel {...props} documentPath="demo.txt" savedRecord={{ id: 'b'.repeat(32), docPath: 'demo.txt' }}/>);
    expect(signal.aborted).toBe(true);
    await act(async () => { finish(response(recognized())); });
    expect(onRecognized).not.toHaveBeenCalled(); expect(createUrl).not.toHaveBeenCalled();
    expect(requests.mock.calls).toHaveLength(1);
  });

  it('识别成功后只用生成 TXT 评测，原图按原尺寸缩放且 Unicode 文字不截断', async () => {
    api(); const page = workspace();
    await uploadAndRecognize();
    await screen.findByText(/已选用识别文字/);
    expect(page.config().docPath).toBe(textPath); expect(page.config().recognitionPath).toBe(recognitionPath);
    expect(page.config().recognitionSource).toEqual(original()); expect(page.config().docImport).toMatchObject({ kind: 'document', relative_path: textPath, file_name: '法规.txt' });
    expect(toRequest(page.config()).doc_path).toBe(textPath); expect(toRequest(page.config())).not.toHaveProperty('recognitionPath');
    expect(screen.getByRole('button', { name: '开始评测' })).toBeEnabled();
    expect(page.run).not.toHaveBeenCalled(); expect(requests.mock.calls.some(([path]) => path === '/api/evaluate')).toBe(false);
    const picture = screen.getByRole('img', { name: '识别原图与所选区域' });
    expect(picture).toHaveAttribute('viewBox', '0 0 200 100'); expect(picture.querySelector('image')).toHaveAttribute('href', 'blob:recognition-test');
    expect(picture.querySelector('polygon')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /😀未经批准/ }));
    expect(picture.querySelector('polygon')).toHaveAttribute('points', '0,0 100,0 100,20 0,20');
    expect(screen.getByText('置信度 0.63 · 低置信')).toBeInTheDocument(); expect(screen.getByText('置信度未提供')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '定位逐句证据' })).not.toBeInTheDocument();
    expect(screen.getByText(/本页先展示识别结果/)).toBeInTheDocument();
  });

  it.each((Object.keys(RECOGNITION_MESSAGES) as RecognitionStatus[]).filter((status) => status !== 'ok'))('状态 %s 有可读文案、不重试、不替换评测文档', async (status) => {
    api(); requests.mockImplementation(async (path: string) => path.includes('/inputs?') ? response(original()) : response({ kind: 'ocr', status }));
    const page = workspace(); await uploadAndRecognize();
    expect(await screen.findByText(RECOGNITION_MESSAGES[status])).toBeInTheDocument();
    expect(page.config().docPath).toBe(''); expect(screen.getByRole('button', { name: '开始评测' })).toBeDisabled();
    expect(requests).toHaveBeenCalledTimes(2); expect(createUrl).not.toHaveBeenCalled(); expect(page.run).not.toHaveBeenCalled();
  });

  it('保留重复命中的所有候选、原判定、缺失 Trace 与真实空列表', async () => {
    api(); workspace({ config: retainedConfig(), savedRecord: { id: recordId, docPath: textPath } });
    await screen.findByText(/已选用识别文字/);
    await userEvent.click(screen.getByRole('button', { name: '定位逐句证据' }));
    await screen.findByText('重复命中，请选择位置 · 2 处');
    expect(screen.getByText('构造测试模型 · 5 题')).toBeInTheDocument();
    expect(screen.getByText('原判定 supported')).toBeInTheDocument();
    expect(screen.getByText('原记录没有逐句证据（Trace 缺失）。')).toBeInTheDocument();
    expect(screen.getByText('原逐句证据损坏，无法定位。')).toBeInTheDocument();
    expect(screen.getByText('原记录的陈述列表为空。')).toBeInTheDocument();
    expect(screen.getByText('未在识别文字中找到引文')).toBeInTheDocument();
    expect(screen.getByText('原陈述没有引文')).toBeInTheDocument(); expect(screen.getByText('原引文格式不合法')).toBeInTheDocument();
    const picture = screen.getByRole('img', { name: '识别原图与所选区域' });
    await userEvent.click(screen.getByRole('button', { name: '构造测试模型 第1题 第1句 命中1' }));
    expect(picture.querySelector('polygon')).toHaveAttribute('points', '0,0 100,0 100,20 0,20');
    await userEvent.click(screen.getByRole('button', { name: '构造测试模型 第1题 第1句 命中2' }));
    expect(picture.querySelector('polygon')).toHaveAttribute('points', '0,30 100,30 100,50 0,50');
    expect(screen.getByRole('button', { name: '构造测试模型 第1题 第1句 命中1' })).toHaveAttribute('aria-pressed', 'false');
    expect(within(screen.getByRole('button', { name: '构造测试模型 第1题 第1句 命中2' })).getByText('置信度未提供')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '打开已有定位' }));
    await screen.findByText('重复命中，请选择位置 · 2 处');
    const locationCalls = requests.mock.calls.filter(([path]) => String(path).endsWith('/source-locations'));
    expect(locationCalls).toHaveLength(2); expect(locationCalls[0][1].method).toBe('POST'); expect(locationCalls[1][1].method).toBeUndefined();
  });

  it('不同评测文档不提供定位入口，不能用旧记录猜测绑定', async () => {
    api(); workspace({ config: retainedConfig(), savedRecord: { id: recordId, docPath: 'demo.txt' } });
    await screen.findByText(/已选用识别文字/);
    expect(screen.queryByRole('button', { name: '定位逐句证据' })).not.toBeInTheDocument();
    expect(requests.mock.calls.some(([path]) => String(path).includes('source-locations'))).toBe(false);
  });

  it('音频 metadata 就绪后才跳转到毫秒起点，保持暂停并释放 Blob', async () => {
    api('asr'); const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
    const page = workspace({ config: retainedConfig('asr') });
    await screen.findByText(/已选用识别文字/);
    expect(screen.getByText(/仅支持 16kHz 单声道 WAV/)).toBeInTheDocument();
    const audio = screen.getByLabelText('识别原音频') as HTMLAudioElement;
    await userEvent.click(screen.getByRole('button', { name: /第一段/ })); expect(audio.currentTime).toBe(0);
    fireEvent.loadedMetadata(audio); expect(audio.currentTime).toBe(1.25);
    await userEvent.click(screen.getByRole('button', { name: /第二段/ })); expect(audio.currentTime).toBe(3);
    expect(play).not.toHaveBeenCalled(); page.unmount(); expect(revokeUrl).toHaveBeenCalledWith('blob:recognition-test');
  });

  it('切换资料撤下原图和定位并释放 Blob，不继续显示旧原件', async () => {
    api(); const page = workspace({ config: retainedConfig(), savedRecord: { id: recordId, docPath: textPath } });
    await screen.findByText(/已选用识别文字/);
    await userEvent.click(screen.getByRole('button', { name: '示例资料' }));
    expect(screen.queryByRole('img', { name: '识别原图与所选区域' })).not.toBeInTheDocument();
    expect(page.config().recognitionPath).toBeUndefined(); expect(page.config().recognitionSource).toBeUndefined();
    expect(page.config().docPath).toBe('demo.txt'); expect(revokeUrl).toHaveBeenCalledWith('blob:recognition-test');
  });

  it('识别读取未结束时锁定配置与运行，卸载中止迟到响应', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const page = workspace({ config: retainedConfig() });
    await waitFor(() => expect(requests).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: '示例资料' })).toBeDisabled(); expect(screen.getByRole('button', { name: '开始评测' })).toBeDisabled();
    const signal = requests.mock.calls[0][1].signal as AbortSignal;
    page.unmount(); expect(signal.aborted).toBe(true);
    await act(async () => { finish(response(recognized())); });
    expect(createUrl).not.toHaveBeenCalled(); expect(requests).toHaveBeenCalledTimes(1);
  });

  it('记录切换后忽略旧定位的迟到响应', async () => {
    api();
    const props = { source: original(), recognitionPath, documentPath: textPath, disabled: false, readOnly: false, onBusy: vi.fn(), onSource: vi.fn(), onRecognized: vi.fn() };
    const page = render(<RecognitionPanel {...props} savedRecord={{ id: recordId, docPath: textPath }}/>);
    await screen.findByText(/已选用识别文字/);
    let finish!: (value: Response) => void;
    requests.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    await userEvent.click(screen.getByRole('button', { name: '定位逐句证据' }));
    const signal = requests.mock.calls.at(-1)![1].signal as AbortSignal;
    page.rerender(<RecognitionPanel {...props} savedRecord={{ id: 'b'.repeat(32), docPath: textPath }}/>);
    expect(signal.aborted).toBe(true);
    await act(async () => { finish(response(located())); });
    expect(screen.queryByText('构造测试模型 · 5 题')).not.toBeInTheDocument();
  });

  it.each(['READ_ONLY', 'STATIC_SITE', 'BYOK'] as const)('%s 禁用识别入口，程序触发也不请求接口', async (flag) => {
    mode[flag] = true; workspace();
    const entry = screen.getByRole('button', { name: '图片/音频识别' }); expect(entry).toBeDisabled();
    fireEvent.click(entry); expect(screen.queryByLabelText('从电脑选择图片')).not.toBeInTheDocument(); expect(requests).not.toHaveBeenCalled();
  });

  it('只读 prop 也禁用入口', () => {
    workspace({ readOnly: true }); expect(screen.getByRole('button', { name: '图片/音频识别' })).toBeDisabled(); expect(requests).not.toHaveBeenCalled();
  });
});
