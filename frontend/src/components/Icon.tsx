import type { CSSProperties } from 'react';

export type IconName = 'shield' | 'overview' | 'layers' | 'chart' | 'trace' | 'file' | 'play' | 'refresh' | 'arrow' | 'check' | 'alert' | 'copy' | 'chevron' | 'folder' | 'link' | 'pulse' | 'pen' | 'upload';
const paths: Record<IconName, React.ReactNode> = {
  upload: <><path d="M12 16V3m-4 4 4-4 4 4M4 15v5h16v-5"/></>,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6z"/><path d="m8 12 3 3 5-6"/></>,
  overview: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
  layers: <><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5m-18 5 9 5 9-5"/></>,
  chart: <><path d="M4 4v16h17"/><path d="M8 15v-4m5 4V7m5 8v-6"/></>,
  trace: <><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="12" r="2"/><path d="M6 7v10m0-9c0 3 10 1 10 4"/></>,
  file: <><path d="M14 3H5v18h14V8z"/><path d="M14 3v5h5M8 12h8m-8 4h6"/></>,
  play: <path d="m8 4 12 8-12 8z"/>,
  refresh: <><path d="M20 8V3l-4 4M4 16v5l4-4"/><path d="M19 7A8 8 0 0 0 5 7m0 10a8 8 0 0 0 14 0"/></>,
  arrow: <path d="M5 12h14m-5-5 5 5-5 5"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  alert: <><path d="m12 3 10 18H2z"/><path d="M12 9v5m0 3v.1"/></>,
  copy: <><rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/></>,
  chevron: <path d="m9 5 7 7-7 7"/>,
  folder: <path d="M3 6h7l2 3h9v11H3z"/>,
  link: <><path d="m10 14 4-4m-6 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m4 0 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0"/></>,
  pulse: <path d="M3 12h4l2-6 4 12 2-6h6"/>,
  pen: <><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/></>,
};

export function Icon({ name, size = 18, className = '', style }: { name: IconName; size?: number; className?: string; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className} style={style}>{paths[name]}</svg>;
}
