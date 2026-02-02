import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import InstallPrompt from "../../app/components/InstallPrompt";

describe("InstallPrompt", () => {
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    window.matchMedia = originalMatchMedia;
    localStorage.clear();
  });

  it("returns null when app is already installed", () => {
    window.matchMedia = jest.fn().mockImplementation(() => ({
      matches: true,
      media: "(display-mode: standalone)",
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }));

    const { container } = render(<InstallPrompt />);

    expect(container.firstChild).toBeNull();
  });

  it("shows prompt after beforeinstallprompt and handles install", async () => {
    const prompt = jest.fn();
    const userChoice = Promise.resolve({ outcome: "accepted" });
    const removeListenerSpy = jest.spyOn(window, "removeEventListener");

    const { unmount } = render(<InstallPrompt />);

    const event = new Event("beforeinstallprompt");
    Object.assign(event, {
      prompt,
      userChoice,
      preventDefault: jest.fn(),
    });

    act(() => {
      window.dispatchEvent(event);
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
      window.dispatchEvent(event);
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
});
