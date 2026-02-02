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

  useEffect(() => {
    // Check if running in standalone mode (already installed and opened)
    if (globalThis.matchMedia("(display-mode: standalone)").matches ||
        (globalThis.navigator as any).standalone === true) {
      setAppState("standalone");
      return;
    }

    // Check if user previously dismissed the prompt
    const dismissed = localStorage.getItem("installPromptDismissed");
    if (dismissed) {
      const dismissedTime = parseInt(dismissed);
      const threeDays = 3 * 24 * 60 * 60 * 1000;
      if (Date.now() - dismissedTime < threeDays) {
        return;
      }
    }

    // Check if app was already installed (but browsing via browser)
    const wasInstalled = localStorage.getItem("appWasInstalled");
    if (wasInstalled === "true") {
      setAppState("installed");
      setTimeout(() => setShowPrompt(true), 3000);
      return;
    }

    // Listen for beforeinstallprompt event
    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e as BeforeInstallPromptEvent);
      setAppState("installable");
      // Show prompt after 10 seconds to not be annoying
      setTimeout(() => setShowPrompt(true), 10000);
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

    console.log(`User ${outcome} the install prompt`);

    if (outcome === "accepted") {
      localStorage.setItem("appWasInstalled", "true");
    }

    setDeferredPrompt(null);
    setShowPrompt(false);
  };

  const handleOpenApp = () => {
    // Try to open the installed PWA
    const appUrl = globalThis.location.origin;
    globalThis.location.href = appUrl;
  };

  const handleDismiss = () => {
    setShowPrompt(false);
    // Show again after 3 days
    localStorage.setItem("installPromptDismissed", Date.now().toString());
  };

  // Don't show anything if in standalone mode or not ready
  if (appState === "standalone" || !showPrompt) {
    return null;
  }

  // Show "Open App" button if already installed
  if (appState === "installed") {
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
              Open OptionsTaxHub from your home screen for the best experience.
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleOpenApp}
                className="flex-1 bg-green-500 hover:bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                Open App
              </button>
              <button
                onClick={handleDismiss}
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

  return null;
}
