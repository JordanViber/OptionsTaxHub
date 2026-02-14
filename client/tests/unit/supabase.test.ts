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
} from "../../lib/supabase";

describe("lib/supabase", () => {
  beforeEach(() => {
    jest.clearAllMocks();
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

      const result = await signUp("test@example.com", "password123");
      expect(mockAuth.signUp).toHaveBeenCalledWith({
        email: "test@example.com",
        password: "password123",
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

      const result = await signIn("test@example.com", "password123");
      expect(mockAuth.signInWithPassword).toHaveBeenCalledWith({
        email: "test@example.com",
        password: "password123",
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
});
