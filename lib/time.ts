/**
 * Parse an ISO 8601 datetime string as UTC.
 *
 * The backend serialises datetime.now(timezone.utc) as "2026-05-20T04:12:03+00:00".
 * Older rows in the DB were stored with datetime.utcnow() and serialise without a
 * timezone suffix (e.g. "2026-05-20T04:12:03").  Per the ISO 8601 / ECMAScript spec,
 * a datetime string without a timezone designator is interpreted as *local* time by
 * most JS engines, causing a UTC+N offset error in the displayed age.
 *
 * This helper appends "Z" when no timezone information is present so the value is
 * always treated as UTC regardless of the user's local timezone.
 */
export function parseUTC(iso: string): Date {
  if (!iso) return new Date(NaN);
  // Already has timezone info (+HH:MM, -HH:MM, or Z)
  if (/[Z+\-]\d*$/.test(iso.trim())) return new Date(iso);
  // Bare UTC string — append Z so the browser treats it as UTC
  return new Date(iso + "Z");
}

/**
 * Return a human-readable "time ago" string for a UTC ISO datetime.
 * e.g. "just now", "3m ago", "2h ago", "5d ago"
 */
export function timeAgo(iso: string): string {
  const diff = Date.now() - parseUTC(iso).getTime();
  if (diff < 0) return "just now";
  const mins = Math.floor(diff / 60_000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}
