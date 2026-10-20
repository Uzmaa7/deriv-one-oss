'use strict';

import { apiFetch } from '@/lib/api';

/** Fire-and-forget frontend analytics — POST /api/events (204). */
export function trackEvent(event: string, properties: Record<string, unknown> = {}): void {
  void apiFetch('/api/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event, properties }),
  }).catch(() => {});
}
