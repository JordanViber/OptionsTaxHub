import { act, renderHook, waitFor } from "@testing-library/react";
import { usePushNotifications } from "@/app/hooks/usePushNotifications";
import { getSupabaseClient } from "@/lib/supabase";

const VAPID_PUBLIC_KEY =
  "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U";

function installBrowserPush() {
  const unsubscribe = jest.fn().mockResolvedValue(true);
  const subscription = {
    endpoint: "https://fcm.googleapis.com/fcm/send/device",
    unsubscribe,
    toJSON() {
      return {
        endpoint: this.endpoint,
        expirationTime: null,
        keys: { p256dh: "device-p256dh", auth: "device-auth" },
      };
    },
  };
  const subscribe = jest.fn().mockResolvedValue(subscription);
  Object.defineProperty(globalThis, "PushManager", {
    configurable: true,
    value: function PushManager() {
      return undefined;
    },
  });
  Object.defineProperty(globalThis, "Notification", {
    configurable: true,
    value: {
      permission: "granted",
      requestPermission: jest.fn().mockResolvedValue("granted"),
    },
  });
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { ready: Promise.resolve({ pushManager: { subscribe } }) },
  });
  return { subscribe, unsubscribe, subscription };
}

describe("usePushNotifications", () => {
  const refreshSession = jest.fn();
  const fetchMock = jest.fn();

  beforeEach(() => {
    process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY = VAPID_PUBLIC_KEY;
    refreshSession.mockReset();
    fetchMock.mockReset();
    global.fetch = fetchMock;
    (getSupabaseClient as jest.Mock).mockReturnValue({
      auth: { refreshSession },
    });
  });

  async function readyHook() {
    const browser = installBrowserPush();
    const rendered = renderHook(() => usePushNotifications());
    await waitFor(() => {
      expect(rendered.result.current.isSupported).toBe(true);
      expect(rendered.result.current.permission).toBe("granted");
    });
    return { ...rendered, ...browser };
  }

  it("does not subscribe a guest and leaves the browser subscription unset", async () => {
    refreshSession.mockResolvedValue({
      data: { session: null },
      error: { message: "no session" },
    });
    const { result, subscribe } = await readyHook();

    let created: PushSubscription | null = null;
    await act(async () => {
      created = await result.current.subscribe();
    });

    expect(created).toBeNull();
    expect(result.current.subscription).toBeNull();
    expect(result.current.lastError).toMatch(/sign in/i);
    expect(subscribe).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the refreshed access token and stores the subscription only after the server accepts it", async () => {
    refreshSession.mockResolvedValue({
      data: { session: { access_token: "access-token" } },
      error: null,
    });
    fetchMock.mockResolvedValue({ ok: true, status: 200 });
    const { result, subscription } = await readyHook();

    let created: PushSubscription | null = null;
    await act(async () => {
      created = await result.current.subscribe();
    });

    expect(created).toBe(subscription);
    expect(result.current.subscription).toBe(subscription);
    expect(result.current.lastError).toBeNull();
    expect(refreshSession).toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/push/subscribe"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer access-token",
          "Content-Type": "application/json",
        }),
      }),
    );
    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(body.endpoint).toBe(subscription.endpoint);
    expect(body.keys).toEqual({ p256dh: "device-p256dh", auth: "device-auth" });
  });

  it.each([401, 409])(
    "leaves the subscription unset when the server responds %s",
    async (status) => {
      refreshSession.mockResolvedValue({
        data: { session: { access_token: "access-token" } },
        error: null,
      });
      fetchMock.mockResolvedValue({ ok: false, status });
      const { result, unsubscribe } = await readyHook();

      let created: PushSubscription | null = null;
      await act(async () => {
        created = await result.current.subscribe();
      });

      expect(created).toBeNull();
      expect(result.current.subscription).toBeNull();
      expect(unsubscribe).toHaveBeenCalled();
    },
  );

  it("keeps the browser subscription when unsubscribe has no session", async () => {
    refreshSession
      .mockResolvedValueOnce({
        data: { session: { access_token: "access-token" } },
        error: null,
      })
      .mockResolvedValueOnce({ data: { session: null }, error: null });
    fetchMock.mockResolvedValue({ ok: true, status: 200 });
    const { result, unsubscribe } = await readyHook();

    await act(async () => {
      await result.current.subscribe();
    });
    expect(result.current.subscription).not.toBeNull();

    let removed = true;
    await act(async () => {
      removed = await result.current.unsubscribe();
    });

    expect(removed).toBe(false);
    expect(unsubscribe).not.toHaveBeenCalled();
    expect(result.current.subscription).not.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("removes the server row before the browser subscription", async () => {
    refreshSession.mockResolvedValue({
      data: { session: { access_token: "access-token" } },
      error: null,
    });
    fetchMock.mockResolvedValue({ ok: true, status: 200 });
    const { result, unsubscribe, subscription } = await readyHook();

    await act(async () => {
      await result.current.subscribe();
    });
    let removed = false;
    await act(async () => {
      removed = await result.current.unsubscribe();
    });

    expect(removed).toBe(true);
    expect(result.current.subscription).toBeNull();
    expect(unsubscribe).toHaveBeenCalled();
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining("/push/unsubscribe"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer access-token",
        }),
      }),
    );
    const unsubscribeOrder = unsubscribe.mock.invocationCallOrder[0];
    const fetchOrder = fetchMock.mock.invocationCallOrder[1];
    expect(fetchOrder).toBeLessThan(unsubscribeOrder);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1].body)).endpoint).toBe(
      subscription.endpoint,
    );
  });
});
