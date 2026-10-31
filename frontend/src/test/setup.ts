import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

// jsdom has no layout engine, so scrollTo only ever printed "Not implemented" to stderr. Workflow views
// scroll to top on every step change, which buried real failures under hundreds of those lines.
Object.defineProperty(window, 'scrollTo', { value: () => {}, writable: true });

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
