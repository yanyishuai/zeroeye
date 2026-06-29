import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthenticationError, get, isApiError, post } from "./api";
function jsonResponse(body: unknown, init: ResponseInit & { headers?: Record<string, string> }) {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return new Response(JSON.stringify(body), { ...init, headers });
}

describe("api error handling", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("returns successful 2xx responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ ok: true }, { status: 200, headers: { "X-Request-ID": "req-200" } }),
      ),
    );

    const result = await get<{ ok: boolean }>("/health");
    expect(result.status).toBe(200);
    expect(result.data.ok).toBe(true);
    expect(result.requestId).toBe("req-200");
  });

  it("rejects 403 JSON errors with structured ApiError fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              message: "Forbidden",
              details: { reason: "insufficient_scope" },
              path: "/profile",
            },
            requestId: "req-403",
          },
          { status: 403 },
        ),
      ),
    );

    await expect(get("/profile", undefined, { retries: 0 })).rejects.toMatchObject({
      code: 403,
      message: "Forbidden",
      requestId: "req-403",
      path: "/profile",
      details: { reason: "insufficient_scope" },
    });
  });

  it("rejects 429 rate-limit JSON errors", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ message: "Too many requests", requestId: "req-429" }, { status: 429 }),
      ),
    );

    const error = await get("/orders").catch((value) => value);
    expect(isApiError(error)).toBe(true);
    expect(error).toMatchObject({ code: 429, message: "Too many requests", requestId: "req-429" });
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it("rejects 500 text errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("upstream unavailable", {
          status: 500,
          statusText: "Internal Server Error",
          headers: { "Content-Type": "text/plain", "X-Request-ID": "req-500" },
        }),
      ),
    );

    await expect(get("/reports")).rejects.toMatchObject({
      code: 500,
      message: "upstream unavailable",
      requestId: "req-500",
    });
  });

  it("maps aborted requests to timeout ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(
        Object.assign(new Error("The operation was aborted."), { name: "AbortError" }),
      ),
    );

    await expect(get("/slow", undefined, { retries: 0 })).rejects.toMatchObject({
      code: 408,
      message: "Request timed out",
    });
  });
});

describe("token refresh handling", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("deduplicates concurrent refresh attempts and retries once", async () => {
    localStorage.setItem("auth_token", "stale-token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ message: "Unauthorized" }, { status: 401 }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ message: "Unauthorized" }, { status: 401 }),
      )
      .mockResolvedValueOnce(jsonResponse({ token: "fresh-token" }, { status: 200 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }, { status: 200 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const [first, second] = await Promise.all([
      get("/profile-a", undefined, { retries: 0 }).catch((error) => error),
      get("/profile-b", undefined, { retries: 0 }).catch((error) => error),
    ]);

    expect(first).toMatchObject({ status: 200, data: { ok: true } });
    expect(second).toMatchObject({ status: 200, data: { ok: true } });
    expect(localStorage.getItem("auth_token")).toBe("fresh-token");

    const refreshCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes("/auth/refresh"),
    );
    expect(refreshCalls).toHaveLength(1);
  });

  it("clears auth state when refresh fails", async () => {
    localStorage.setItem("auth_token", "stale-token");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse({ message: "Unauthorized" }, { status: 401 }))
        .mockResolvedValueOnce(jsonResponse({ message: "invalid refresh" }, { status: 401 })),
    );

    const error = await get("/profile", undefined, { retries: 0 }).catch((value) => value);
    expect(error).toBeInstanceOf(AuthenticationError);
    expect(localStorage.getItem("auth_token")).toBeNull();
  });

  it("does not refresh-loop on the refresh endpoint itself", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ message: "Unauthorized" }, { status: 401 })),
    );

    await expect(post("/auth/refresh", {}, undefined, { retries: 0 })).rejects.toMatchObject({
      code: 401,
      message: "Unauthorized",
    });
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });
});
