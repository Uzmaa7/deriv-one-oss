"use strict";

const STORAGE_PREFIX = "derivone-pending-avatar:";

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error ?? new Error("Could not read image."));
    reader.readAsDataURL(file);
  });
}

export function storePendingAvatar(agentId: string, base64: string): void {
  if (!agentId || !base64) return;
  try {
    sessionStorage.setItem(STORAGE_PREFIX + agentId, base64);
  } catch {
    // quota / private mode — retry upload is best-effort
  }
}

export function peekPendingAvatar(agentId: string): string | null {
  if (!agentId) return null;
  try {
    return sessionStorage.getItem(STORAGE_PREFIX + agentId);
  } catch {
    return null;
  }
}

export function clearPendingAvatar(agentId: string): void {
  if (!agentId) return;
  try {
    sessionStorage.removeItem(STORAGE_PREFIX + agentId);
  } catch {
    /* ignore */
  }
}

export function pendingAvatarToFile(base64: string): File {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new File([bytes], "avatar.png", { type: "image/png" });
}
