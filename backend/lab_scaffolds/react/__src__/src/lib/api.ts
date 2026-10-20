export const APP_ID = "{{APP_ID}}";
export const BASE = `/api/apps/${APP_ID}`;

export async function runPython<T = Record<string, unknown>>(
  functionName: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  const res = await fetch(`${BASE}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ function: functionName, payload }),
  });
  if (!res.ok) throw new Error(`run failed: ${res.status}`);
  return res.json() as Promise<T>;
}
