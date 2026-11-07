/** Vite's base includes the trailing slash; the default build remains /api/. */
export function apiPath(path: string): string {
  return `${import.meta.env.BASE_URL}api/${path}`;
}
