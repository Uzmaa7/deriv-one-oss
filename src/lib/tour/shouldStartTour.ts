'use strict';

import { CURRENT_TOUR_VERSION, INACTIVE_THRESHOLD_DAYS } from './tourConfig';

export function shouldStartTour(
  lastSeenTourVersion: string | null | undefined,
  lastLoginAt: string | null | undefined,
): boolean {
  if (lastSeenTourVersion == null) {
    return true;
  }

  const daysSince = lastLoginAt
    ? (Date.now() - new Date(lastLoginAt).getTime()) / (1000 * 60 * 60 * 24)
    : Infinity;

  return daysSince > INACTIVE_THRESHOLD_DAYS && lastSeenTourVersion !== CURRENT_TOUR_VERSION;
}
