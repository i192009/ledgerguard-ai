export type Row = Record<string, any>;
const base = import.meta.env.VITE_API_URL || "/api/v1";
export const accessToken = () => sessionStorage.getItem("lg_access");
export function saveSession(data: Row) {
  sessionStorage.setItem("lg_access", data.access_token);
  sessionStorage.setItem("lg_refresh", data.refresh_token);
}
export function clearSession() {
  sessionStorage.removeItem("lg_access");
  sessionStorage.removeItem("lg_refresh");
}
let refreshing: Promise<boolean> | null = null;
async function refresh() {
  if (!refreshing)
    refreshing = (async () => {
      const token = sessionStorage.getItem("lg_refresh");
      if (!token) return false;
      const result = await fetch(`${base}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: token }),
      });
      if (!result.ok) {
        clearSession();
        return false;
      }
      saveSession(await result.json());
      return true;
    })().finally(() => {
      refreshing = null;
    });
  return refreshing;
}
export async function api<T = Row>(
  path: string,
  options: RequestInit = {},
  retry = true,
): Promise<T> {
  const response = await fetch(base + path, {
    ...options,
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...(accessToken() ? { Authorization: `Bearer ${accessToken()}` } : {}),
      ...options.headers,
    },
  });
  if (response.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await refresh()) return api(path, options, false);
    clearSession();
    window.dispatchEvent(new Event("session-expired"));
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      error.error?.message ||
        error.detail?.[0]?.msg ||
        "Request failed. Please try again.",
    );
  }
  return response.json();
}
export const post = (path: string, data: Row = {}) =>
  api(path, { method: "POST", body: JSON.stringify(data) });
export async function download(path: string, name: string) {
  const response = await fetch(base + path, {
    headers: { Authorization: `Bearer ${accessToken()}` },
  });
  if (!response.ok) throw new Error("Download failed");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
export async function streamRun(
  runId: string,
  onEvent: (event: Row) => void,
  signal: AbortSignal,
  after?: string,
) {
  const response = await fetch(
    `${base}/agents/runs/${runId}/events${after ? `?after=${encodeURIComponent(after)}` : ""}`,
    { headers: { Authorization: `Bearer ${accessToken()}` }, signal },
  );
  if (!response.ok || !response.body)
    throw new Error("Progress stream unavailable");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = block.split("\n").find((line) => line.startsWith("data: "));
      if (data) onEvent(JSON.parse(data.slice(6)));
    }
  }
}
