/** Minimal Server-Sent Events reader for streamed POST responses.
 *
 * EventSource cannot issue a POST, so the agent stream is read straight off the
 * fetch body instead.
 */
export async function readSse<T>(
  response: Response,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  const body = response.body;
  if (!body) throw new Error("Streaming is not supported by this browser");

  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        const line = frame.split("\n").find((row) => row.startsWith("data:"));
        if (!line) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          onEvent(JSON.parse(payload) as T);
        } catch {
          // A truncated frame is not worth tearing the stream down for.
        }
      }
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}
