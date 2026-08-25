import "@testing-library/jest-dom";

// Polyfill TextEncoder/TextDecoder for jsdom (needed by MUI DataGrid)
if (globalThis.TextEncoder === undefined) {
  const { TextEncoder, TextDecoder } = require("node:util");
  globalThis.TextEncoder = TextEncoder;
  globalThis.TextDecoder = TextDecoder;
}

// Mock window.matchMedia
Object.defineProperty(globalThis, "matchMedia", {
  writable: true,
  value: jest.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
});

// Mock Supabase with proper session structure
const mockSession = {
  access_token: "mock-jwt-token",
  token_type: "bearer",
  expires_in: 3600,
  refresh_token: "mock-refresh-token",
  user: {
    id: "test-user-id",
    email: "test@example.com",
    email_confirmed_at: "2025-01-01T00:00:00Z",
    user_metadata: {},
  },
};

jest.mock("@/lib/supabase", () => ({
  getSupabaseClient: jest.fn(() => ({
    auth: {
      getSession: jest.fn(() =>
        Promise.resolve({
          data: { session: mockSession },
          error: null,
        }),
      ),
      signInWithPassword: jest.fn(() => Promise.resolve({ error: null })),
      signUp: jest.fn(() =>
        Promise.resolve({
          data: { user: { id: "test-id" } },
          error: null,
        }),
      ),
      signOut: jest.fn(() => Promise.resolve({ error: null })),
      onAuthStateChange: jest.fn(() => ({
        data: { subscription: { unsubscribe: jest.fn() } },
      })),
      updateUser: jest.fn(() => Promise.resolve({ data: {}, error: null })),
      exchangeCodeForSession: jest.fn(() => Promise.resolve({ error: null })),
      verifyOtp: jest.fn(() => Promise.resolve({ error: null })),
      resend: jest.fn(() => Promise.resolve({ error: null })),
      setSession: jest.fn(() => Promise.resolve({ error: null })),
    },
  })),
  getSession: jest.fn(() => Promise.resolve(mockSession)),
  signIn: jest.fn(() => Promise.resolve({ user: { id: "test-id" } })),
  signUp: jest.fn(() => Promise.resolve({ user: { id: "test-id" } })),
  signOut: jest.fn(() => Promise.resolve()),
  getCurrentUser: jest.fn(() => Promise.resolve({ id: "test-user-id" })),
  resetPasswordForEmail: jest.fn(() => Promise.resolve()),
  updatePassword: jest.fn(() => Promise.resolve()),
  establishRecoverySession: jest.fn(() => Promise.resolve()),
  getPasswordResetRedirectTo: jest.fn(() => "http://localhost/auth/reset-password"),
  getEmailConfirmRedirectTo: jest.fn(
    () => "http://localhost/auth/confirm-email",
  ),
  resendSignupConfirmation: jest.fn(() => Promise.resolve()),
  consumeEmailConfirmLink: jest.fn(() =>
    Promise.resolve({ session: null, consumedLink: false }),
  ),
  isEmailConfirmed: (user: { email_confirmed_at?: string | null } | null) =>
    Boolean(user?.email_confirmed_at),
  isEmailNotConfirmedError: (err: { code?: string; message?: string } | string) => {
    const text = `${typeof err === "string" ? err : `${err?.code ?? ""} ${err?.message ?? ""}`}`.toLowerCase();
    return (
      text.includes("email_not_confirmed") ||
      text.includes("email not confirmed")
    );
  },
}));

jest.mock("next/link", () => {
  const React = require("react");
  return React.forwardRef(
    (
      {
        children,
        href,
        ...rest
      }: { children?: React.ReactNode; href: string; [key: string]: unknown },
      ref: React.Ref<HTMLAnchorElement>,
    ) =>
      React.createElement("a", { href, ref, ...rest }, children),
  );
});
