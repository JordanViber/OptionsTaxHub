// Undo the global mock from jest.setup.ts so we test the REAL supabase.ts module
jest.unmock("@/lib/supabase");

// Set env vars BEFORE any module loads so supabase.ts top-level reads find them
process.env.NEXT_PUBLIC_SUPABASE_URL = "https://test.supabase.co";
process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "test-anon-key";

// Mock the supabase-js library so createClient returns our controlled mock
const mockAuth = {
  signUp: jest.fn(),
  signInWithPassword: jest.fn(),
  signOut: jest.fn(),
  getSession: jest.fn(),
  getUser: jest.fn(),
  resetPasswordForEmail: jest.fn(),
  updateUser: jest.fn(),
  exchangeCodeForSession: jest.fn(),
  verifyOtp: jest.fn(),
};

const mockSupabaseInstance = { auth: mockAuth };

jest.mock("@supabase/supabase-js", () => ({
  createClient: jest.fn(() => mockSupabaseInstance),
}));

// Now import — env vars are set, createClient is mocked
import {
  getSupabaseClient,
  signUp,
  signIn,
  signOut,
  getSession,
  getCurrentUser,
  resetPasswordForEmail,
  updatePassword,
  establishRecoverySession,
  getPasswordResetRedirectTo,
} from "../../lib/supabase";

describe("lib/supabase", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.history.pushState({}, "", "/");
    window.location.hash = "";
  });

  describe("getSupabaseClient", () => {
    it("returns a client with auth methods", () => {
      const client = getSupabaseClient();
      expect(client).toBeDefined();
      expect(client.auth).toBeDefined();
    });

    it("returns the same singleton on subsequent calls", () => {
      const first = getSupabaseClient();
      const second = getSupabaseClient();
      expect(first).toBe(second);
    });

    it("throws when environment variables are missing", () => {
      // Save original env vars
      const origUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
      const origKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

      try {
        // Clear env vars and reset modules to force re-evaluation
        delete process.env.NEXT_PUBLIC_SUPABASE_URL;
        delete process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

        jest.resetModules();

        // Re-require the module with missing env vars
        const {
          getSupabaseClient: freshGetClient,
        } = require("../../lib/supabase");

        expect(() => freshGetClient()).toThrow(
          "Supabase environment variables are required.",
        );
      } finally {
        // Restore env vars
        process.env.NEXT_PUBLIC_SUPABASE_URL = origUrl;
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = origKey;
      }
    });
  });

  describe("signUp", () => {
    it("calls supabase auth.signUp and returns data", async () => {
      const fakeData = { user: { id: "1" }, session: null };
      mockAuth.signUp.mockResolvedValue({ data: fakeData, error: null });

      const result = await signUp("test@example.com", "password123"); // NOSONAR typescript:S2068
      expect(mockAuth.signUp).toHaveBeenCalledWith({
        email: "test@example.com",
        password: "password123", // NOSONAR typescript:S2068
      });
      expect(result).toEqual(fakeData);
    });

    it("throws on error", async () => {
      mockAuth.signUp.mockResolvedValue({
        data: null,
        error: new Error("Signup failed"),
      });

      await expect(signUp("test@example.com", "pw")).rejects.toThrow(
        "Signup failed",
      );
    });
  });

  describe("signIn", () => {
    it("calls supabase auth.signInWithPassword and returns data", async () => {
      const fakeData = {
        user: { id: "1" },
        session: { access_token: "tok" },
      };
      mockAuth.signInWithPassword.mockResolvedValue({
        data: fakeData,
        error: null,
      });

      const result = await signIn("test@example.com", "password123"); // NOSONAR typescript:S2068
      expect(mockAuth.signInWithPassword).toHaveBeenCalledWith({
        email: "test@example.com",
        password: "password123", // NOSONAR typescript:S2068
      });
      expect(result).toEqual(fakeData);
    });

    it("throws on error", async () => {
      mockAuth.signInWithPassword.mockResolvedValue({
        data: null,
        error: new Error("Invalid login"),
      });

      await expect(signIn("a@b.com", "wrong")).rejects.toThrow("Invalid login");
    });
  });

  describe("signOut", () => {
    it("calls supabase auth.signOut", async () => {
      mockAuth.signOut.mockResolvedValue({ error: null });
      await signOut();
      expect(mockAuth.signOut).toHaveBeenCalled();
    });

    it("throws on error", async () => {
      mockAuth.signOut.mockResolvedValue({
        error: new Error("Sign out failed"),
      });

      await expect(signOut()).rejects.toThrow("Sign out failed");
    });
  });

  describe("getSession", () => {
    it("returns session data", async () => {
      const fakeSession = { access_token: "tok", user: { id: "1" } };
      mockAuth.getSession.mockResolvedValue({
        data: { session: fakeSession },
        error: null,
      });

      const result = await getSession();
      expect(result).toEqual(fakeSession);
    });

    it("throws on error", async () => {
      mockAuth.getSession.mockResolvedValue({
        data: null,
        error: new Error("Session error"),
      });

      await expect(getSession()).rejects.toThrow("Session error");
    });
  });

  describe("getCurrentUser", () => {
    it("returns user data", async () => {
      const fakeUser = { id: "1", email: "test@example.com" };
      mockAuth.getUser.mockResolvedValue({
        data: { user: fakeUser },
        error: null,
      });

      const result = await getCurrentUser();
      expect(result).toEqual(fakeUser);
    });

    it("throws on error", async () => {
      mockAuth.getUser.mockResolvedValue({
        data: null,
        error: new Error("User error"),
      });

      await expect(getCurrentUser()).rejects.toThrow("User error");
    });
  });

  describe("resetPasswordForEmail", () => {
    it("sends recovery email with redirectTo /auth/reset-password", async () => {
      mockAuth.resetPasswordForEmail.mockResolvedValue({ error: null });
      await resetPasswordForEmail("test@example.com");
      expect(mockAuth.resetPasswordForEmail).toHaveBeenCalledWith(
        "test@example.com",
        expect.objectContaining({
          redirectTo: expect.stringMatching(/\/auth\/reset-password$/),
        }),
      );
      expect(mockAuth.resetPasswordForEmail.mock.calls[0][1].redirectTo).not.toMatch(
        /\/auth\/signin/,
      );
    });

    it("throws on error", async () => {
      mockAuth.resetPasswordForEmail.mockResolvedValue({
        error: new Error("Reset failed"),
      });
      await expect(resetPasswordForEmail("a@b.com")).rejects.toThrow(
        "Reset failed",
      );
    });
  });

  describe("getPasswordResetRedirectTo", () => {
    it("points at the reset-password route on the current origin", () => {
      expect(getPasswordResetRedirectTo()).toBe(
        `${window.location.origin}/auth/reset-password`,
      );
    });
  });

  describe("updatePassword", () => {
    it("calls supabase auth.updateUser with the new password", async () => {
      const fakeData = { user: { id: "1" } };
      mockAuth.updateUser.mockResolvedValue({ data: fakeData, error: null });
      const result = await updatePassword("new-pass-123"); // NOSONAR typescript:S2068
      expect(mockAuth.updateUser).toHaveBeenCalledWith({
        password: "new-pass-123", // NOSONAR typescript:S2068
      });
      expect(result).toEqual(fakeData);
    });

    it("throws on error", async () => {
      mockAuth.updateUser.mockResolvedValue({
        data: null,
        error: new Error("Password too weak"),
      });
      await expect(updatePassword("123")).rejects.toThrow("Password too weak");
    });
  });

  describe("establishRecoverySession", () => {
    it("exchanges a PKCE code from the URL", async () => {
      window.history.pushState(
        {},
        "",
        "/auth/reset-password?code=pkce-recovery-code",
      );
      mockAuth.exchangeCodeForSession.mockResolvedValue({ error: null });
      mockAuth.getSession.mockResolvedValue({
        data: { session: { access_token: "tok" } },
        error: null,
      });

      await establishRecoverySession();
      expect(mockAuth.exchangeCodeForSession).toHaveBeenCalledWith(
        "pkce-recovery-code",
      );
    });

    it("verifies a token_hash recovery link", async () => {
      window.history.pushState(
        {},
        "",
        "/auth/reset-password?token_hash=hash-token&type=recovery",
      );
      mockAuth.verifyOtp.mockResolvedValue({ error: null });
      mockAuth.getSession.mockResolvedValue({
        data: { session: { access_token: "tok" } },
        error: null,
      });

      await establishRecoverySession();
      expect(mockAuth.verifyOtp).toHaveBeenCalledWith({
        token_hash: "hash-token",
        type: "recovery",
      });
    });

    it("uses an existing PASSWORD_RECOVERY session", async () => {
      mockAuth.getSession.mockResolvedValue({
        data: { session: { access_token: "tok" } },
        error: null,
      });
      await establishRecoverySession();
      expect(mockAuth.exchangeCodeForSession).not.toHaveBeenCalled();
    });

    it("throws when the hash contains an expired-link error", async () => {
      window.location.hash =
        "#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired";
      await expect(establishRecoverySession()).rejects.toThrow(
        /invalid or has expired/i,
      );
    });

    it("throws when there is no recovery session", async () => {
      mockAuth.getSession.mockResolvedValue({
        data: { session: null },
        error: null,
      });
      await expect(establishRecoverySession()).rejects.toThrow("otp_expired");
    });
  });
});
