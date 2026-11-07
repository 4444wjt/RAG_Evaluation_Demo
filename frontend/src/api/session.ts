import { BYOK } from '../config';

const SESSION_KEY = 'veriscope-visitor-session';
let memorySession: string | undefined;

/** Only this random session identifier is persisted. Model credentials never enter browser storage. */
export function sessionHeaders(): Record<string, string> {
  if (!BYOK) return {};
  if (!memorySession) {
    try {
      const saved = sessionStorage.getItem(SESSION_KEY);
      if (saved && /^[a-f0-9]{64}$/.test(saved)) memorySession = saved;
    } catch { /* Storage may be disabled; keep the same session in memory. */ }
    if (!memorySession) {
      memorySession = Array.from(crypto.getRandomValues(new Uint8Array(32)), (value) => value.toString(16).padStart(2, '0')).join('');
      try { sessionStorage.setItem(SESSION_KEY, memorySession); } catch { /* in-memory session remains stable */ }
    }
  }
  return { 'X-VeriScope-Session': memorySession };
}
