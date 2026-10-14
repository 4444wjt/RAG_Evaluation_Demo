import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceView } from '../features/WorkspaceView';
import { FileImportControl } from '../features/FileImportControl';
import { initialConfig, toRequest } from '../state';
import type { WorkspaceConfig } from '../state';
import type { ImportedInput } from '../api/inputFiles';

const documentPath = `work/uploads/${'a'.repeat(32)}.txt`;
const testsetPath = `work/uploads/${'b'.repeat(32)}.csv`;
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
let requests: ReturnType<typeof vi.fn>;

beforeEach(() => {
  requests = vi.fn();
  vi.stubGlobal('fetch', requests);
});

function workspace(options: { config?: Partial<WorkspaceConfig>; readOnly?: boolean } = {}) {
  let latest: WorkspaceConfig = { ...initialConfig, ...options.config };
  const run = vi.fn();
  function Harness() {
    const [config, setConfig] = useState(latest);
    return <WorkspaceView config={config} onChange={(value) => { latest = value; setConfig(value); }}
      onRun={run} running={false} readOnly={options.readOnly} hasLiveResult={false} error={null} configurationCheck={null}/>;
  }
  return { ...render(<Harness/>), config: () => latest, run };
}

function upload(name: string, file: File) {
  fireEvent.change(screen.getByLabelText(name), { target: { files: [file] } });
}

function imported(file: File, kind: 'document' | 'testset' = 'document'): ImportedInput {
  return { kind, file_name: file.name, relative_path: kind === 'document' ? documentPath : testsetPath, size_bytes: file.size };
}

describe('Workspace examples and real computer-file import', () => {
  it('starts with three usable examples and switches them without an API call', async () => {
    const page = workspace();
    expect(screen.getByRole('button', { name: '示例资料' })).toHaveAttribute('aria-pressed', 'true');
    const choices = within(screen.getByRole('radiogroup', { name: '示例评测资料' }));
    expect(choices.getAllByRole('radio')).toHaveLength(3);
    expect(choices.getByRole('radio', { name: /默认示例文档/ })).toBeChecked();
    await userEvent.click(choices.getByRole('radio', { name: /存款保险/ }));
    expect(page.config().docPath).toBe('scenarios/deposit_insurance.txt');
    expect(screen.getByRole('button', { name: '开始评测' })).toBeEnabled();
    expect(requests).not.toHaveBeenCalled();
    expect(page.run).not.toHaveBeenCalled();
  });

  it('requires a computer file before running and restores the chosen example when switching back', async () => {
    const page = workspace();
    await userEvent.click(screen.getByRole('radio', { name: /信息披露/ }));
    await userEvent.click(screen.getByRole('button', { name: '电脑文件' }));
    expect(page.config().docPath).toBe('');
    expect(screen.getByRole('button', { name: '开始评测' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    expect(page.run).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: '示例资料' }));
    expect(page.config().docPath).toBe('scenarios/information_disclosure.txt');
    expect(screen.getByRole('radio', { name: /信息披露/ })).toBeChecked();
    expect(requests).not.toHaveBeenCalled();
  });

  it('uploads original bytes only to the import endpoint and passes only the returned path into evaluation', async () => {
    const page = workspace();
    const file = new File(['这是评测资料。'], '金融 资料.txt', { type: 'text/plain' });
    requests.mockResolvedValueOnce(response(imported(file)));
    await userEvent.click(screen.getByRole('button', { name: '电脑文件' }));
    upload('从电脑选择评测资料', file);
    await screen.findByText('已导入评测服务');
    expect(requests).toHaveBeenCalledTimes(1);
    const [url, options] = requests.mock.calls[0];
    const target = new URL(url, 'http://localhost');
    expect(target.pathname).toBe('/api/inputs');
    expect(target.searchParams.get('kind')).toBe('document');
    expect(target.searchParams.get('filename')).toBe(file.name);
    expect(options).toMatchObject({ method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
    expect(options.body).toBe(file);
    expect(page.config().docPath).toBe(documentPath);
    expect(page.config().docImport).toEqual(imported(file));
    expect(toRequest(page.config())).toEqual({ ...toRequest(initialConfig), doc_path: documentPath });
    expect(page.run).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '开始评测' })).toBeEnabled();
  });

  it('imports a custom testset independently of the selected document', async () => {
    const page = workspace();
    const file = new File(['question,ground_truth\n产品名称,星语'], '固定题集.csv', { type: 'text/csv' });
    requests.mockResolvedValueOnce(response(imported(file, 'testset')));
    await userEvent.selectOptions(screen.getByLabelText('测试集来源'), 'custom');
    upload('从电脑选择测试集', file);
    await screen.findByText('已导入评测服务');
    expect(new URL(requests.mock.calls[0][0], 'http://localhost').searchParams.get('kind')).toBe('testset');
    expect(page.config().docPath).toBe('demo.txt');
    expect(page.config().testsetPath).toBe(testsetPath);
    expect(toRequest(page.config())).toEqual({ ...toRequest(initialConfig), testset_path: testsetPath });
    expect(toRequest(page.config())).not.toHaveProperty('testsetImport');
    expect(page.run).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: /使用示例测试集/ }));
    expect(page.config().testsetPath).toBe('testset_example.csv');
    expect(page.config().testsetImport).toBeUndefined();
  });

  it('keeps the last imported document if a replacement fails', async () => {
    const file = new File(['原资料'], '原资料.txt');
    const page = workspace({ config: { docPath: documentPath, docImport: imported(file) } });
    requests.mockRejectedValueOnce(new Error('offline'));
    upload('从电脑选择评测资料', new File(['替换资料'], '新资料.txt'));
    expect(await screen.findByRole('alert')).toHaveTextContent('无法连接本地导入服务');
    expect(page.config().docPath).toBe(documentPath);
    expect(page.config().docImport).toEqual(imported(file));
    expect(screen.getByRole('button', { name: '开始评测' })).toBeEnabled();
    expect(page.run).not.toHaveBeenCalled();
  });

  it('blocks duplicate uploads, configuration changes and evaluation while importing', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const page = workspace();
    await userEvent.click(screen.getByRole('button', { name: '电脑文件' }));
    const file = new File(['正文'], '资料.txt');
    upload('从电脑选择评测资料', file);
    await screen.findByText('正在导入评测资料…');
    expect(screen.getByLabelText('从电脑选择评测资料')).toBeDisabled();
    expect(screen.getByRole('button', { name: '示例资料' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '开始评测' })).toBeDisabled();
    upload('从电脑选择评测资料', file);
    expect(requests).toHaveBeenCalledTimes(1);
    expect(page.run).not.toHaveBeenCalled();
    await act(async () => { finish(response(imported(file))); });
    expect(screen.getByRole('button', { name: '开始评测' })).toBeEnabled();
  });

  it('allows browsing both sources in read-only mode but never imports or evaluates', async () => {
    const page = workspace({ readOnly: true });
    await userEvent.click(screen.getByRole('button', { name: '电脑文件' }));
    expect(screen.getByLabelText('从电脑选择评测资料')).toBeDisabled();
    expect(screen.getByRole('button', { name: /选择电脑文件/ })).toBeDisabled();
    upload('从电脑选择评测资料', new File(['正文'], '资料.txt'));
    fireEvent.drop(screen.getByRole('button', { name: /选择电脑文件/ }), { dataTransfer: { files: [new File(['正文'], '资料.txt')] } });
    expect(screen.getByRole('button', { name: '开始评测' })).toBeDisabled();
    expect(requests).not.toHaveBeenCalled();
    expect(page.run).not.toHaveBeenCalled();
  });

  it('aborts the upload on unmount and does not apply a late response', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const onImported = vi.fn();
    const onBusy = vi.fn();
    const page = render(<FileImportControl kind="document" disabled={false} readOnly={false} onImported={onImported} onBusy={onBusy}/>);
    const file = new File(['正文'], '资料.txt');
    upload('从电脑选择评测资料', file);
    await waitFor(() => expect(requests).toHaveBeenCalledTimes(1));
    const signal = requests.mock.calls[0][1].signal as AbortSignal;
    expect(signal.aborted).toBe(false);
    page.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => { finish(response(imported(file))); });
    expect(onImported).not.toHaveBeenCalled();
  });
});
