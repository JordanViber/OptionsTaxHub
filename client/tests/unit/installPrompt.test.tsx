import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import InstallPrompt from "../../app/components/InstallPrompt";

describe("InstallPrompt", () => {
  const originalMatchMedia = globalThis.matchMedia;
  const originalInnerWidth = globalThis.innerWidth;

  function mockStandaloneMode(isStandalone: boolean) {
    globalThis.matchMedia = jest.fn().mockImplementation((query: string) => {
      let matches = false;
      if (query === "(display-mode: standalone)") {
        matches = isStandalone;
      }
      return {
        matches,
        media: query,
        onchange: null,
        addListener: jest.fn(),
        removeListener: jest.fn(),
        addEventListener: jest.fn(),
        removeEventListener: jest.fn(),
        dispatchEvent: jest.fn(),
      };
    });
  }

  function mockMobileDevice() {
    Object.defineProperty(globalThis.navigator, "userAgent", {
      value:
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
      configurable: true,
    });
    Object.defineProperty(globalThis, "innerWidth", {
      value: 375,
      configurable: true,
    });
    globalThis.matchMedia = jest.fn().mockImplementation((query: string) => {
      let matches = false;
      if (query === "(hover: none) and (pointer: coarse)") {
        matches = true;
      }
      return {
        matches,
        media: query,
        onchange: null,
        addListener: jest.fn(),
        removeListener: jest.fn(),
        addEventListener: jest.fn(),
        removeEventListener: jest.fn(),
        dispatchEvent: jest.fn(),
      };
    });
  }

  beforeEach(() => {
    jest.useFakeTimers();
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    globalThis.matchMedia = originalMatchMedia;
    Object.defineProperty(globalThis, "innerWidth", {
      value: originalInnerWidth,
      configurable: true,
    });
    localStorage.clear();
    sessionStorage.clear();
  });

  it("returns null when app is already installed (standalone mode)", () => {
    mockStandaloneMode(true);

    const { container } = render(<InstallPrompt />);
    expect(container.firstChild).toBeNull();
  });

  it("shows prompt after beforeinstallprompt and handles install", async () => {
    const prompt = jest.fn();
    const userChoice = Promise.resolve({ outcome: "accepted" });
    const removeListenerSpy = jest.spyOn(globalThis, "removeEventListener");

    const { unmount } = render(<InstallPrompt />);

    const event = new Event("beforeinstallprompt");
    Object.assign(event, {
      prompt,
      userChoice,
      preventDefault: jest.fn(),
    });

    act(() => {
      globalThis.dispatchEvent(event);
      jest.advanceTimersByTime(10000);
    });

    await waitFor(() => {
      expect(screen.getByText("Install OptionsTaxHub")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Install"));

    await waitFor(() => {
      expect(prompt).toHaveBeenCalled();
    });

    unmount();
    expect(removeListenerSpy).toHaveBeenCalledWith(
      "beforeinstallprompt",
      expect.any(Function),
    );
  });

  it("handles dismiss action and stores timestamp", async () => {
    const setItemSpy = jest.spyOn(Storage.prototype, "setItem");

    render(<InstallPrompt />);

    const event = new Event("beforeinstallprompt");
    Object.assign(event, {
      prompt: jest.fn(),
      userChoice: Promise.resolve({ outcome: "dismissed" }),
      preventDefault: jest.fn(),
    });

    act(() => {
      globalThis.dispatchEvent(event);
      jest.advanceTimersByTime(10000);
    });

    await waitFor(() => {
      expect(screen.getByText("Install OptionsTaxHub")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Not now"));

    expect(setItemSpy).toHaveBeenCalledWith(
      "installPromptDismissed",
      expect.any(String),
    );
  });

  it("does not show prompt if dismissed less than 3 days ago", () => {
    // Set dismissal time to 1 day ago (within 3-day cooldown)
    localStorage.setItem(
      "installPromptDismissed",
      (Date.now() - 1 * 24 * 60 * 60 * 1000).toString(),
    );

    const { container } = render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(15000);
    });

    expect(container.firstChild).toBeNull();
  });

  it("shows prompt if dismissed more than 3 days ago", async () => {
    // Set dismissal time to 4 days ago (past 3-day cooldown)
    localStorage.setItem(
      "installPromptDismissed",
      (Date.now() - 4 * 24 * 60 * 60 * 1000).toString(),
    );

    render(<InstallPrompt />);

    const event = new Event("beforeinstallprompt");
    Object.assign(event, {
      prompt: jest.fn(),
      userChoice: Promise.resolve({ outcome: "dismissed" }),
      preventDefault: jest.fn(),
    });

    act(() => {
      globalThis.dispatchEvent(event);
      jest.advanceTimersByTime(10000);
    });

    await waitFor(() => {
      expect(screen.getByText("Install OptionsTaxHub")).toBeInTheDocument();
    });
  });

  it("shows installed message when app was installed (desktop)", async () => {
    localStorage.setItem("appWasInstalled", "true");

    render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(screen.getByText("App Already Installed")).toBeInTheDocument();
    });

    // Desktop shows "Got it" button and no "Open App" button
    expect(screen.getByText("Got it")).toBeInTheDocument();
    expect(screen.queryByText("Open App")).not.toBeInTheDocument();

    // Desktop message mentions start menu
    expect(screen.getByText(/start menu/i)).toBeInTheDocument();
  });

  it("shows installed message with Open App button on mobile", async () => {
    mockMobileDevice();
    localStorage.setItem("appWasInstalled", "true");

    render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(screen.getByText("App Already Installed")).toBeInTheDocument();
    });

    // Mobile shows "Open App" button
    expect(screen.getByText("Open App")).toBeInTheDocument();
    // Mobile message mentions home screen
    expect(screen.getByText(/home screen/i)).toBeInTheDocument();
  });

  it("dismisses installed message and stores timestamp", async () => {
    const setItemSpy = jest.spyOn(Storage.prototype, "setItem");

    localStorage.setItem("appWasInstalled", "true");

    render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(screen.getByText("App Already Installed")).toBeInTheDocument();
    });

    // Desktop has "Got it" button
    fireEvent.click(screen.getByText("Got it"));

    expect(setItemSpy).toHaveBeenCalledWith(
      "installedMessageDismissed",
      expect.any(String),
    );
  });

  it("does not show installed message if dismissed less than 7 days ago", () => {
    localStorage.setItem("appWasInstalled", "true");
    // Dismissed 2 days ago — within 7-day cooldown
    localStorage.setItem(
      "installedMessageDismissed",
      (Date.now() - 2 * 24 * 60 * 60 * 1000).toString(),
    );

    const { container } = render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(5000);
    });

    expect(container.firstChild).toBeNull();
  });

  it("shows installed message if dismissed more than 7 days ago", async () => {
    localStorage.setItem("appWasInstalled", "true");
    // Dismissed 8 days ago — past 7-day cooldown
    localStorage.setItem(
      "installedMessageDismissed",
      (Date.now() - 8 * 24 * 60 * 60 * 1000).toString(),
    );

    render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(screen.getByText("App Already Installed")).toBeInTheDocument();
    });
  });

  it("handles appinstalled event", async () => {
    render(<InstallPrompt />);

    // Trigger beforeinstallprompt first
    const event = new Event("beforeinstallprompt");
    Object.assign(event, {
      prompt: jest.fn(),
      userChoice: Promise.resolve({ outcome: "dismissed" }),
      preventDefault: jest.fn(),
    });

    act(() => {
      globalThis.dispatchEvent(event);
      jest.advanceTimersByTime(10000);
    });

    await waitFor(() => {
      expect(screen.getByText("Install OptionsTaxHub")).toBeInTheDocument();
    });

    // Now fire appinstalled
    act(() => {
      globalThis.dispatchEvent(new Event("appinstalled"));
    });

    expect(localStorage.getItem("appWasInstalled")).toBe("true");
    // Prompt should be hidden after install
    expect(screen.queryByText("Install OptionsTaxHub")).not.toBeInTheDocument();
  });

  it("handleOpenApp navigates and sets sessionStorage", async () => {
    mockMobileDevice();
    localStorage.setItem("appWasInstalled", "true");

    render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(screen.getByText("Open App")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Open App"));

    // Verify sessionStorage was set for the loop guard
    expect(sessionStorage.getItem("lastOpenAppAttempt")).toBeTruthy();
    // Note: jsdom doesn't support navigation — the href assignment is a no-op,
    // but the code path is still exercised for coverage.
  });

  it("handleOpenApp dismisses if called within 1 second (loop guard)", async () => {
    mockMobileDevice();
    localStorage.setItem("appWasInstalled", "true");

    render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(screen.getByText("Open App")).toBeInTheDocument();
    });

    // Set the lastOpenAppAttempt AFTER advancing timers so it's within 1 second
    // of the current fake-timer Date.now()
    sessionStorage.setItem("lastOpenAppAttempt", Date.now().toString());

    fireEvent.click(screen.getByText("Open App"));

    // Should dismiss rather than navigate (calls handleDismissInstalled)
    expect(localStorage.getItem("installedMessageDismissed")).toBeTruthy();
  });

  it("dismisses mobile installed view", async () => {
    mockMobileDevice();
    localStorage.setItem("appWasInstalled", "true");

    render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(screen.getByText("App Already Installed")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Dismiss"));

    expect(localStorage.getItem("installedMessageDismissed")).toBeTruthy();
  });

  it("stores appWasInstalled on accepted install", async () => {
    const prompt = jest.fn();
    const userChoice = Promise.resolve({ outcome: "accepted" as const });

    render(<InstallPrompt />);

    const event = new Event("beforeinstallprompt");
    Object.assign(event, {
      prompt,
      userChoice,
      preventDefault: jest.fn(),
    });

    act(() => {
      globalThis.dispatchEvent(event);
      jest.advanceTimersByTime(10000);
    });

    await waitFor(() => {
      expect(screen.getByText("Install")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Install"));

    await waitFor(() => {
      expect(localStorage.getItem("appWasInstalled")).toBe("true");
    });
  });

  it("returns null when not ready (showPrompt=false)", () => {
    const { container } = render(<InstallPrompt />);
    // No event fired, no installed state — should show nothing
    expect(container.firstChild).toBeNull();
  });

  it("handleOpenApp proceeds normally when last attempt was over 1 second ago", async () => {
    mockMobileDevice();
    localStorage.setItem("appWasInstalled", "true");

    // Set a previous attempt well over 1 second ago
    sessionStorage.setItem(
      "lastOpenAppAttempt",
      (Date.now() - 5000).toString(),
    );

    render(<InstallPrompt />);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    await waitFor(() => {
      expect(screen.getByText("Open App")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Open App"));

    // Should update the sessionStorage timestamp (not dismiss)
    expect(sessionStorage.getItem("lastOpenAppAttempt")).toBeTruthy();
    // Should NOT set installedMessageDismissed (that's the loop guard behavior)
    expect(localStorage.getItem("installedMessageDismissed")).toBeNull();
  });

  it("returns null when installable but deferredPrompt is cleared", async () => {
    // Trigger beforeinstallprompt then install with "dismissed" outcome
    // After handleInstall completes, deferredPrompt=null → renders null
    const prompt = jest.fn();
    const userChoice = Promise.resolve({ outcome: "dismissed" as const });

    const { container } = render(<InstallPrompt />);

    const event = new Event("beforeinstallprompt");
    Object.assign(event, {
      prompt,
      userChoice,
      preventDefault: jest.fn(),
    });

    act(() => {
      globalThis.dispatchEvent(event);
      jest.advanceTimersByTime(10000);
    });

    await waitFor(() => {
      expect(screen.getByText("Install")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Install"));

    await waitFor(() => {
      expect(prompt).toHaveBeenCalled();
    });

    // After dismissed, prompt should be gone — component renders null
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });
});
