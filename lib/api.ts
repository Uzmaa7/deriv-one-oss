'use strict';

/**
 * Same-origin /api calls behind whatever identity proxy fronts this deployment.
 * Sends credentials so browsers attach session cookies where the provider relies
 * on them.
 *
 * VITE_AUTH_TOKEN is a development convenience: when the UI is not running
 * behind the identity proxy there is no token on the request, and this supplies
 * one as a bearer header. It is baked into the built assets, so a production
 * build carrying one would ship a credential to every visitor — leave it unset
 * and put the deployment behind the proxy instead.
 */
export function apiFetch(input: string | URL, init: RequestInit = {}): Promise<Response> {
  const hdrs = new Headers(init.headers ?? undefined);
  const builtIn = typeof import.meta.env?.VITE_AUTH_TOKEN === 'string'
    ? import.meta.env.VITE_AUTH_TOKEN.trim()
    : '';
  if (
    builtIn
    && !hdrs.has('authorization')
    && !hdrs.has('Authorization')
  ) {
    hdrs.set('Authorization', `Bearer ${builtIn}`);
  }

  const { headers: _ignoredHeaders, credentials, ...rest } = init;

  return fetch(input, {
    credentials: credentials ?? 'include',
    ...rest,
    headers: hdrs,
  });
}

/** Flatten a FastAPI error body into a single user-facing string. */
export function extractErrorDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    const msgs = detail.map((e) => (
      e && typeof e === 'object' && 'msg' in e ? String((e as { msg: unknown }).msg) : String(e)
    ));
    return msgs.join('; ');
  }
  return fallback;
}

/** Read an API error response, including non-JSON gateway/proxy failures. */
export async function readApiErrorResponse(res: Response, fallback: string): Promise<string> {
  const text = await res.text();
  if (!text.trim()) {
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      return (
        `${fallback} The server did not return details — it may be unavailable or timed out. `
        + `(HTTP ${res.status})`
      );
    }
    return `${fallback} (HTTP ${res.status})`;
  }
  try {
    return extractErrorDetail(JSON.parse(text), fallback);
  } catch {
    const snippet = text.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 180);
    if (snippet && !snippet.toLowerCase().startsWith('<!doctype')) {
      return `${fallback} ${snippet}`;
    }
    return `${fallback} (HTTP ${res.status})`;
  }
}
