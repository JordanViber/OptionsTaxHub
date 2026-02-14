"use client";

import { useState, useEffect } from "react";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

type AppState = "standalone" | "installable" | "installed" | "not-installable";

export default function InstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] =
    useState<BeforeInstallPromptEvent | null>(null);
  const [showPrompt, setShowPrompt] = useState(false);
  const [appState, setAppState] = useState<AppState>("not-installable");
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    // Detect if device is mobile using multiple methods for reliability
    const isMobileDevice = () => {
      // Check user agent
      const userAgent = globalThis.navigator.userAgent;
      const mobileRegex =
        /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i;

      // Also check viewport width as additional check
      const isNarrowViewport = globalThis.window.innerWidth < 768;

      // Check for touch capability (more reliable indicator of mobile)
      const hasTouch = globalThis.matchMedia(
        "(hover: none) and (pointer: coarse)",
      ).matches;

      // Only consider it mobile if user agent matches AND (has touch or narrow viewport)
      // This prevents false positives on desktop
      return mobileRegex.test(userAgent) && (hasTouch || isNarrowViewport);
    };
    setIsMobile(isMobileDevice());

    // Check if running in standalone mode (already installed and opened)
    if (
      globalThis.matchMedia("(display-mode: standalone)").matches ||
      (globalThis.navigator as any).standalone === true
    ) {
      setAppState("standalone");
      return;
    }

    // Only show the prompt once per browser session
    if (sessionStorage.getItem("installPromptShownThisSession")) {
      return;
    }

    // Check if user previously dismissed the prompt
    const dismissed = localStorage.getItem("installPromptDismissed");
    if (dismissed) {
      const dismissedTime = Number.parseInt(dismissed, 10);
      const threeDays = 3 * 24 * 60 * 60 * 1000;
      if (Date.now() - dismissedTime < threeDays) {
        return;
      }
    }

    // Check if app was already installed (but browsing via browser)
    const wasInstalled = localStorage.getItem("appWasInstalled");
    if (wasInstalled === "true") {
      setAppState("installed");
      setTimeout(() => {
        setShowPrompt(true);
        sessionStorage.setItem("installPromptShownThisSession", "true");
      }, 3000);
      return;
    }

    // Listen for beforeinstallprompt event
    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e as BeforeInstallPromptEvent);
      setAppState("installable");
      // Show prompt after 10 seconds to not be annoying
      setTimeout(() => {
        setShowPrompt(true);
        sessionStorage.setItem("installPromptShownThisSession", "true");
      }, 10000);
    };

    // Listen for app installed event
    const installedHandler = () => {
      localStorage.setItem("appWasInstalled", "true");
      setAppState("installed");
      setShowPrompt(false);
    };

    globalThis.addEventListener("beforeinstallprompt", handler);
    globalThis.addEventListener("appinstalled", installedHandler);

    return () => {
      globalThis.removeEventListener("beforeinstallprompt", handler);
      globalThis.removeEventListener("appinstalled", installedHandler);
    };
  }, []);

  const handleInstall = async () => {
    if (!deferredPrompt) return;

    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;

    if (outcome === "accepted") {
      localStorage.setItem("appWasInstalled", "true");
    }

    setDeferredPrompt(null);
    setShowPrompt(false);
  };

  const handleDismiss = () => {
    setShowPrompt(false);
    // Show again after 3 days
    localStorage.setItem("installPromptDismissed", Date.now().toString());
  };

  const handleDismissInstalled = () => {
    setShowPrompt(false);
    // Don't show this message again for 7 days since app is already installed
    localStorage.setItem("installedMessageDismissed", Date.now().toString());
  };

  const handleOpenApp = () => {
    // Add safeguard: check if we've already tried to open recently to prevent loops
    const lastAttemptTime = sessionStorage.getItem("lastOpenAppAttempt");
    if (lastAttemptTime) {
      const timeSinceAttempt =
        Date.now() - Number.parseInt(lastAttemptTime, 10);
      if (timeSinceAttempt < 1000) {
        // Less than 1 second - likely a refresh loop, don't navigate
        handleDismissInstalled();
        return;
      }
    }

    sessionStorage.setItem("lastOpenAppAttempt", Date.now().toString());
    // On mobile browsers, this will open the installed PWA
    // The browser handles the actual opening - we just navigate to the app URL
    globalThis.location.href = globalThis.location.origin;
  };

  // Don't show anything if in standalone mode
  if (appState === "standalone") {
    return null;
  }

  // Check if installed message was dismissed recently
  if (appState === "installed") {
    const dismissed = localStorage.getItem("installedMessageDismissed");
    if (dismissed) {
      const dismissedTime = Number.parseInt(dismissed, 10);
      const sevenDays = 7 * 24 * 60 * 60 * 1000;
      if (Date.now() - dismissedTime < sevenDays) {
        return null;
      }
    }
  }

  // Don't show if not ready
  if (!showPrompt) {
    return null;
  }

  // Show reminder if already installed
  if (appState === "installed") {
    // On mobile, show "Open App" button that will switch to the installed PWA
    if (isMobile) {
      return (
        <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 md:max-w-sm bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-700 p-4 z-50 animate-slide-up">
          <div className="flex items-start gap-3">
            <div className="flex-shrink-0 w-12 h-12 bg-gradient-to-br from-green-500 to-green-700 rounded-lg flex items-center justify-center text-white text-2xl font-bold">
              ✓
            </div>
            <div className="flex-1">
              <h3 className="font-semibold text-gray-900 dark:text-white mb-1">
                App Already Installed
              </h3>
              <p className="text-sm text-gray-600 dark:text-gray-300 mb-3">
                Open OptionsTaxHub from your home screen for the best
                experience.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={handleOpenApp}
                  className="flex-1 bg-green-500 hover:bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                >
                  Open App
                </button>
                <button
                  onClick={handleDismissInstalled}
                  className="px-4 py-2 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg text-sm font-medium transition-colors"
                >
                  Dismiss
                </button>
              </div>
            </div>
          </div>
        </div>
      );
    }

    // On desktop, just show a simple dismissible message (don't try to open app)
    return (
      <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 md:max-w-sm bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-700 p-4 z-50 animate-slide-up">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 w-12 h-12 bg-gradient-to-br from-green-500 to-green-700 rounded-lg flex items-center justify-center text-white text-2xl font-bold">
            ✓
          </div>
          <div className="flex-1">
            <h3 className="font-semibold text-gray-900 dark:text-white mb-1">
              App Already Installed
            </h3>
            <p className="text-sm text-gray-600 dark:text-gray-300 mb-3">
              Launch OptionsTaxHub from your applications menu or start menu for
              the best experience.
            </p>
            <button
              onClick={handleDismissInstalled}
              className="w-full bg-gray-500 hover:bg-gray-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              Got it
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Show "Install" button if installable but not yet installed
  if (appState === "installable" && deferredPrompt) {
    return (
      <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 md:max-w-sm bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-700 p-4 z-50 animate-slide-up">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 w-12 h-12 bg-gradient-to-br from-blue-500 to-blue-700 rounded-lg flex items-center justify-center text-white text-2xl font-bold">
            $
          </div>
          <div className="flex-1">
            <h3 className="font-semibold text-gray-900 dark:text-white mb-1">
              Install OptionsTaxHub
            </h3>
            <p className="text-sm text-gray-600 dark:text-gray-300 mb-3">
              Get quick access to your portfolio and receive tax alerts on your
              home screen.
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleInstall}
                className="flex-1 bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                Install
              </button>
              <button
                onClick={handleDismiss}
                className="px-4 py-2 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg text-sm font-medium transition-colors"
              >
                Not now
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }
}
