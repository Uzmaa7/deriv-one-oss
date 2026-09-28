import { apiFetch } from "@/lib/api";

export function trackEvent(event: string, properties?: Record<string, unknown>): void {
  apiFetch("/api/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event, properties: properties ?? {} }),
  }).catch(() => {
    // fire-and-forget — never block the user
  });
}
