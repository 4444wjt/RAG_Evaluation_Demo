export interface SourceEntry {
  path: string;
  sha256: string;
  title: string;
  authority: 'official' | 'fictional';
  issuer: string | null;
  documentNumber: string | null;
  promulgated: string | null;
  effective: string | null;
  revision: string | null;
  sourceUrl: string | null;
  sourcePublisher: string | null;
  obtained: string | null;
  coverage: { kind: 'full' | 'excerpt'; sections: string[] };
  scope: {
    subjects: string | null;
    region: string | null;
    conditions: string | null;
  };
  fieldSources: Partial<Record<string, string>>;
  archives: Array<{ path: string; rawSha256: string }>;
}

export interface SourceRegistry {
  schema: number;
  hashMethod: 'sha256-lf';
  note: string;
  documents: SourceEntry[];
}

export interface SourceDocument {
  path: string;
  sha256: string;
}

export interface SourceStatusResult {
  state: '信息齐备' | '缺少版本' | '适用范围待确认';
  missing: string[];
  fictional: boolean;
}

const versionFields = [
  ['issuer', '制定机关'],
  ['promulgated', '公布日期'],
  ['effective', '施行日期'],
  ['sourceUrl', '来源网址'],
  ['obtained', '资料取得日期'],
] as const;

const scopeFields = [
  ['subjects', '适用对象'],
  ['region', '适用地域'],
  ['conditions', '适用条件'],
] as const;

function isMissing(value: string | null | undefined): boolean {
  return value == null || value.trim() === '';
}

/** 来源登记完整性只用于展示，不判断真实性、有效性，也不参与门禁。 */
export function sourceStatus(entry: SourceEntry | null): SourceStatusResult {
  if (entry === null) {
    return { state: '缺少版本', missing: ['来源登记'], fictional: false };
  }

  const fictional = entry.authority === 'fictional';
  const missingVersion = versionFields
    .filter(([field]) => isMissing(entry[field]))
    .map(([, label]) => label);

  if (missingVersion.length > 0) {
    return { state: '缺少版本', missing: missingVersion, fictional };
  }

  const missingScope: string[] = scopeFields
    .filter(([field]) => isMissing(entry.scope[field]))
    .map(([, label]) => label);

  if (entry.coverage.kind === 'excerpt' && entry.coverage.sections.length === 0) {
    missingScope.push('节选范围');
  }

  if (missingScope.length > 0) {
    return { state: '适用范围待确认', missing: missingScope, fictional };
  }

  return { state: '信息齐备', missing: [], fictional };
}

/** 只有路径与资料哈希同时匹配才关联登记；不按文件名推断来源。 */
export function lookupSource(
  document: SourceDocument | null,
  registry: { readonly documents: readonly SourceEntry[] },
): SourceEntry | null {
  if (document === null || isMissing(document.path) || isMissing(document.sha256)) {
    return null;
  }

  return registry.documents.find(
    (entry) => entry.path === document.path && entry.sha256 === document.sha256,
  ) ?? null;
}


export type SourceLookupMissReason = 'missing_identity' | 'hash_mismatch' | 'unregistered';

/** 仅在lookupSource未命中后分类；同路径探测只解释原因，不关联任何登记信息。 */
export function sourceLookupMissReason(
  document: SourceDocument | null,
  registry: { readonly documents: readonly SourceEntry[] },
): SourceLookupMissReason {
  if (document === null || isMissing(document.path) || isMissing(document.sha256)) return 'missing_identity';
  return registry.documents.some((entry) => entry.path === document.path) ? 'hash_mismatch' : 'unregistered';
}
