import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

class SwResponse {
  status: number;
  private readonly headerMap: Map<string, string>;

  constructor(
    _body = "",
    init: { status?: number; statusText?: string; headers?: Record<string, string> } = {},
  ) {
    this.status = init.status ?? 200;
    this.headerMap = new Map(Object.entries(init.headers ?? {}));
  }

  get headers() {
    return { get: (name: string) => this.headerMap.get(name) ?? null };
  }

  clone() {
    return this;
  }
}

type FetchListener = (event: {
  request: {
    url: string;
    method: string;
    mode: string;
    headers: { get: (name: string) => string | null };
  };
  respondWith: (result: Promise<unknown>) => void;
}) => void;

function bootServiceWorker() {
  const listeners: Record<string, FetchListener> = {};
  const puts: string[] = [];
  const fetchMock = jest.fn(async () => {
    return new SwResponse("ok", {
      status: 200,
      headers: { "content-type": "application/javascript" },
    });
  });
  const cache = {
    addAll: async () => undefined,
    match: async () => undefined,
    put: async (request: { url: string }) => {
      puts.push(request.url);
    },
  };
  const caches = {
    open: jest.fn(async () => cache),
    match: jest.fn(async () => undefined),
    keys: jest.fn(async () => []),
    delete: jest.fn(async () => true),
  };
  const origin = "http://localhost:3000";
  const sandbox = {
    console,
    URL,
    Response: SwResponse,
    Map,
    Date,
    Array,
    Promise,
    setInterval: () => 0,
    fetch: fetchMock,
    caches,
    clients: {
      claim: () => undefined,
      matchAll: async () => [],
      openWindow: async () => undefined,
    },
    self: {
      location: { origin },
      addEventListener: (type: string, handler: FetchListener) => {
        listeners[type] = handler;
      },
      skipWaiting: () => undefined,
    },
    globalThis: {
      skipWaiting: () => undefined,
      clients: { claim: () => undefined },
      registration: {
        scope: `${origin}/`,
        showNotification: async () => undefined,
      },
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../../public/sw.js"), "utf8"),
    sandbox,
  );
  return { listeners, puts, fetchMock, caches };
}

function dispatch(listener: FetchListener, url: string, method = "GET") {
  let handled: Promise<unknown> | undefined;
  listener({
    request: {
      url,
      method,
      mode: "cors",
      headers: { get: () => null },
    },
    respondWith: (result) => {
      handled = result;
    },
  });
  return handled;
}

describe("service worker cache scope", () => {
  it("never caches Supabase requests or backend /health GETs", async () => {
    const { listeners, puts, fetchMock, caches } = bootServiceWorker();
    const listener = listeners.fetch;
    const urls = [
      "http://vgrlucxqncajjdoaoctq.localhost:54321/auth/v1/user",
      "http://vgrlucxqncajjdoaoctq.localhost:54321/rest/v1/profiles",
      "http://localhost:8011/health",
      "http://localhost:8011/push/subscriptions",
      "http://localhost:3000/health",
    ];

    urls.forEach((url) => {
      expect(dispatch(listener, url)).toBeUndefined();
    });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(caches.open).not.toHaveBeenCalled();
    expect(puts).toEqual([]);
  });

  it("cache-first only for same-origin static assets", async () => {
    const { listeners, puts, fetchMock } = bootServiceWorker();
    const listener = listeners.fetch;
    const cached = [
      "http://localhost:3000/_next/static/chunks/app.js",
      "http://localhost:3000/icons/icon-192x192.png",
      "http://localhost:3000/manifest.json",
      "http://localhost:3000/fonts/inter.woff2",
    ];

    for (let index = 0; index < cached.length; index += 1) {
      const pending = dispatch(listener, cached[index]);
      expect(pending).toBeDefined();
      await pending;
    }
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });

    expect(fetchMock).toHaveBeenCalledTimes(cached.length);
    expect(puts).toEqual(cached);
    expect(
      dispatch(listener, "http://localhost:3000/api/portfolio/history"),
    ).toBeUndefined();
    expect(
      dispatch(listener, "http://localhost:3000/_next/static/chunks/app.js", "POST"),
    ).toBeUndefined();
  });
});
