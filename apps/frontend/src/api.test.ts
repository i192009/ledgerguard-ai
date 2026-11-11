import { afterEach, describe, expect, it, vi } from "vitest";
import { api, clearSession, saveSession } from "./api";
afterEach(() => {
  vi.unstubAllGlobals();
  clearSession();
});
describe("authenticated API client", () => {
  it("sends the access token and preserves exact monetary strings", async () => {
    saveSession({ access_token: "access", refresh_token: "refresh" });
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ amount: "100.01" })));
    vi.stubGlobal("fetch", fetcher);
    expect(await api("/transactions")).toEqual({ amount: "100.01" });
    expect(fetcher.mock.calls[0][1].headers.Authorization).toBe(
      "Bearer access",
    );
  });
  it("surfaces domain errors instead of reporting success", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({
              error: { message: "Maker cannot decide their own proposal" },
            }),
            { status: 403 },
          ),
        ),
    );
    await expect(api("/approvals/x")).rejects.toThrow(
      "Maker cannot decide their own proposal",
    );
  });
  it("rotates an expired token once and retries the original request", async () => {
    saveSession({ access_token: "old", refresh_token: "raw-refresh" });
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ access_token: "new", refresh_token: "rotated" }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: "tenant-owned" })),
      );
    vi.stubGlobal("fetch", fetcher);
    expect(await api("/accounts")).toEqual({ id: "tenant-owned" });
    expect(fetcher.mock.calls[2][1].headers.Authorization).toBe("Bearer new");
  });
});
