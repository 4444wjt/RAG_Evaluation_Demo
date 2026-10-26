import { describe, expect, it } from 'vitest';
import { lookupSource, sourceLookupMissReason, sourceStatus } from '../utils/sourceStatus';
import type { SourceEntry } from '../utils/sourceStatus';

function completeEntry(): SourceEntry {
  return {
    path: 'scenarios/example.txt',
    sha256: 'a'.repeat(64),
    title: '测试资料',
    authority: 'official',
    issuer: '测试制定机关',
    documentNumber: null,
    promulgated: '2020-01-01',
    effective: '2020-02-01',
    revision: null,
    sourceUrl: 'https://example.gov.cn/document',
    sourcePublisher: '测试发布机构',
    obtained: '2026-10-14',
    coverage: { kind: 'excerpt', sections: ['第二条'] },
    scope: { subjects: '测试对象', region: '测试地域', conditions: '无' },
    fieldSources: {},
    archives: [],
  };
}

describe('sourceStatus', () => {
  it('未登记时显示缺少版本，不推断来源', () => {
    expect(sourceStatus(null)).toEqual({
      state: '缺少版本', missing: ['来源登记'], fictional: false,
    });
  });

  it.each([
    ['issuer', '制定机关'],
    ['promulgated', '公布日期'],
    ['effective', '施行日期'],
    ['sourceUrl', '来源网址'],
    ['obtained', '资料取得日期'],
  ] as const)('缺少版本字段 %s 时列出中文字段名', (field, label) => {
    const entry = completeEntry();
    entry[field] = null;
    expect(sourceStatus(entry)).toEqual({
      state: '缺少版本', missing: [label], fictional: false,
    });
  });

  it('版本缺失优先于适用范围缺失，只返回版本字段', () => {
    const entry = completeEntry();
    entry.issuer = null;
    entry.effective = null;
    entry.obtained = null;
    entry.scope.region = null;
    entry.coverage.sections = [];
    expect(sourceStatus(entry)).toEqual({
      state: '缺少版本',
      missing: ['制定机关', '施行日期', '资料取得日期'],
      fictional: false,
    });
  });

  it('空白版本字段仍视为缺失', () => {
    const entry = completeEntry();
    entry.promulgated = '  ';
    expect(sourceStatus(entry).missing).toEqual(['公布日期']);
    expect(sourceStatus(entry).state).toBe('缺少版本');
  });

  it.each([
    ['subjects', '适用对象'],
    ['region', '适用地域'],
    ['conditions', '适用条件'],
  ] as const)('版本齐全但缺少 %s 时显示适用范围待确认', (field, label) => {
    const entry = completeEntry();
    entry.scope[field] = null;
    expect(sourceStatus(entry)).toEqual({
      state: '适用范围待确认', missing: [label], fictional: false,
    });
  });

  it('节选没有登记范围时显示适用范围待确认', () => {
    const entry = completeEntry();
    entry.coverage.sections = [];
    expect(sourceStatus(entry)).toEqual({
      state: '适用范围待确认', missing: ['节选范围'], fictional: false,
    });
  });

  it('按稳定顺序列出所有范围缺失项', () => {
    const entry = completeEntry();
    entry.scope = { subjects: null, region: '', conditions: '  ' };
    entry.coverage.sections = [];
    expect(sourceStatus(entry).missing).toEqual([
      '适用对象', '适用地域', '适用条件', '节选范围',
    ]);
  });

  it('完整资料无需节选条目，信息齐全只表示登记完整', () => {
    const entry = completeEntry();
    entry.coverage = { kind: 'full', sections: [] };
    expect(sourceStatus(entry)).toEqual({
      state: '信息齐备', missing: [], fictional: false,
    });
  });

  it('齐全的节选资料显示信息齐备', () => {
    expect(sourceStatus(completeEntry())).toEqual({
      state: '信息齐备', missing: [], fictional: false,
    });
  });

  it('文号、修订说明与发布页面机构不参与状态判定', () => {
    const entry = completeEntry();
    entry.documentNumber = null;
    entry.revision = null;
    entry.sourcePublisher = null;
    expect(sourceStatus(entry).state).toBe('信息齐备');
  });

  it('虚构资料保留明确标识且不编造缺失版本信息', () => {
    const entry = completeEntry();
    entry.authority = 'fictional';
    entry.issuer = null;
    entry.promulgated = null;
    entry.effective = null;
    entry.sourceUrl = null;
    entry.obtained = null;
    expect(sourceStatus(entry)).toEqual({
      state: '缺少版本',
      missing: ['制定机关', '公布日期', '施行日期', '来源网址', '资料取得日期'],
      fictional: true,
    });
  });

  it('重复判断不修改登记内容', () => {
    const entry = completeEntry();
    const before = JSON.stringify(entry);
    expect(sourceStatus(entry)).toEqual(sourceStatus(entry));
    expect(JSON.stringify(entry)).toBe(before);
  });
});

describe('lookupSource', () => {
  it('路径与哈希同时匹配时返回同一登记条目', () => {
    const entry = completeEntry();
    const registry = { documents: [entry] };
    expect(lookupSource({ path: entry.path, sha256: entry.sha256 }, registry)).toBe(entry);
  });

  it('实时评测没有资料身份时不根据回答或题目推断', () => {
    expect(lookupSource(null, { documents: [completeEntry()] })).toBeNull();
  });

  it('同路径但哈希不同不关联', () => {
    const entry = completeEntry();
    expect(lookupSource({ path: entry.path, sha256: 'b'.repeat(64) }, {
      documents: [entry],
    })).toBeNull();
  });

  it('同哈希但路径不同不关联，也不按文件名回退', () => {
    const entry = completeEntry();
    expect(lookupSource({ path: 'another/example.txt', sha256: entry.sha256 }, {
      documents: [entry],
    })).toBeNull();
  });

  it('缺少哈希时不按路径回退', () => {
    const entry = completeEntry();
    expect(lookupSource({ path: entry.path, sha256: '' }, {
      documents: [entry],
    })).toBeNull();
  });

  it('不自行改写路径、哈希或登记表', () => {
    const entry = completeEntry();
    const registry = { documents: [entry] };
    const before = JSON.stringify(registry);
    expect(lookupSource({ path: `./${entry.path}`, sha256: entry.sha256 }, registry)).toBeNull();
    expect(JSON.stringify(registry)).toBe(before);
  });
});


describe('sourceLookupMissReason', () => {
  it.each([
    null,
    { path: '', sha256: 'a'.repeat(64) },
    { path: 'scenarios/example.txt', sha256: '  ' },
  ])('缺少资料身份时只报告无法核对：%j', (document) => {
    expect(sourceLookupMissReason(document, { documents: [completeEntry()] })).toBe('missing_identity');
  });

  it('同路径不同哈希单独报告版本不一致，不改变严格匹配', () => {
    const registry = { documents: [completeEntry()] };
    const document = { path: 'scenarios/example.txt', sha256: 'b'.repeat(64) };
    expect(lookupSource(document, registry)).toBeNull();
    expect(sourceLookupMissReason(document, registry)).toBe('hash_mismatch');
  });

  it.each(['own/document.txt', './scenarios/example.txt'])('不同路径未登记，不按相同哈希或文件名关联：%s', (path) => {
    const registry = { documents: [completeEntry()] };
    const document = { path, sha256: registry.documents[0].sha256 };
    const before = structuredClone(registry);
    expect(lookupSource(document, registry)).toBeNull();
    expect(sourceLookupMissReason(document, registry)).toBe('unregistered');
    expect(registry).toEqual(before);
  });
});
