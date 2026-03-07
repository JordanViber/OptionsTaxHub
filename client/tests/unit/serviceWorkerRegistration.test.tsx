import { render, waitFor } from "@testing-library/react";
import ServiceWorkerRegistration from "../../app/components/ServiceWorkerRegistration";

describe("ServiceWorkerRegistration", () => {
  const originalServiceWorker = navigator.serviceWorker;
  const originalNodeEnv = process.env.NODE_ENV;
  const serviceWorkerGlobal = globalThis as typeof globalThis & {
    __OPTIONS_TAX_HUB_DISABLE_SW__?: boolean;
  };

  beforeEach(() => {
    jest.useFakeTimers();
    Object.defineProperty(process.env, "NODE_ENV", {
      value: originalNodeEnv,
      configurable: true,
    });
    delete serviceWorkerGlobal.__OPTIONS_TAX_HUB_DISABLE_SW__;
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    Object.defineProperty(process.env, "NODE_ENV", {
      value: originalNodeEnv,
      configurable: true,
    });
    Object.defineProperty(navigator, "serviceWorker", {
      value: originalServiceWorker,
      configurable: true,
    });
    delete serviceWorkerGlobal.__OPTIONS_TAX_HUB_DISABLE_SW__;
  });

  it("registers service worker and schedules updates", async () => {
    const update = jest.fn();
    const register = jest.fn().mockResolvedValue({
      scope: "/app",
      update,
    });

    Object.defineProperty(navigator, "serviceWorker", {
      value: { register },
      configurable: true,
    });

    render(<ServiceWorkerRegistration />);

    await waitFor(() => {
      expect(register).toHaveBeenCalledWith("/sw.js");
    });

    jest.advanceTimersByTime(60000);

    expect(update).toHaveBeenCalled();
  });

  it("handles registration failure silently", async () => {
    const register = jest.fn().mockRejectedValue(new Error("fail"));
    const consoleErrorSpy = jest.spyOn(console, "error").mockImplementation();
    const consoleWarnSpy = jest.spyOn(console, "warn").mockImplementation();

    Object.defineProperty(navigator, "serviceWorker", {
      value: { register },
      configurable: true,
    });

    render(<ServiceWorkerRegistration />);

    await waitFor(() => {
      expect(register).toHaveBeenCalledWith("/sw.js");
    });

    // Service worker registration fails gracefully without console errors
    expect(register).toHaveBeenCalled();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
    expect(consoleWarnSpy).not.toHaveBeenCalled();

    consoleErrorSpy.mockRestore();
    consoleWarnSpy.mockRestore();
  });

  it("does nothing when serviceWorker is unavailable", () => {
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;

    render(<ServiceWorkerRegistration />);
  });

  it("unregisters existing service workers in development instead of registering a new one", async () => {
    serviceWorkerGlobal.__OPTIONS_TAX_HUB_DISABLE_SW__ = true;
    const unregister = jest.fn().mockResolvedValue(true);
    const getRegistrations = jest.fn().mockResolvedValue([{ unregister }]);
    const register = jest.fn().mockResolvedValue({ update: jest.fn() });

    Object.defineProperty(navigator, "serviceWorker", {
      value: { register, getRegistrations },
      configurable: true,
    });

    render(<ServiceWorkerRegistration />);

    await waitFor(() => {
      expect(getRegistrations).toHaveBeenCalled();
    });

    expect(unregister).toHaveBeenCalled();
    expect(register).not.toHaveBeenCalled();
  });
});
