import { renderHook, act, waitFor } from "@testing-library/react";
import { ReactNode } from "react";

// Build the mock Supabase client used by AuthProvider
const mockUnsubscribe = jest.fn();
const mockGetSession = jest.fn();
const mockOnAuthStateChange = jest.fn();
const mockSignInWithPassword = jest.fn();
const mockSignUp = jest.fn();
const mockSignOut = jest.fn();

const mockSupabaseClient = {
  auth: {
    getSession: mockGetSession,
    onAuthStateChange: mockOnAuthStateChange,
    signInWithPassword: mockSignInWithPassword,
    signUp: mockSignUp,
    signOut: mockSignOut,
  },
};

jest.mock("@/lib/supabase", () => ({
  getSupabaseClient: () => mockSupabaseClient,
  getEmailConfirmRedirectTo: (next?: string | null) =>
    next
      ? `http://localhost/auth/confirm-email?next=${next}`
      : "http://localhost/auth/confirm-email",
}));

// Import AFTER mock is set up
import { AuthProvider, useAuth } from "../../app/context/auth";

function createWrapper({ children }: Readonly<{ children: ReactNode }>) {
  return <AuthProvider>{children}</AuthProvider>;
}

describe("Auth Context", () => {
  let authChangeCallback: (event: string, session: any) => void;

  beforeEach(() => {
    jest.clearAllMocks();
    mockGetSession.mockResolvedValue({
      data: { session: null },
    });
    mockOnAuthStateChange.mockImplementation((cb: any) => {
      authChangeCallback = cb;
      return {
        data: { subscription: { unsubscribe: mockUnsubscribe } },
      };
    });
  });

  it("provides loading=true initially then false after session check", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    // After session resolves, loading should be false
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.user).toBeNull();
  });

  it("sets user when session exists on mount", async () => {
    const fakeUser = { id: "u1", email: "test@example.com" };
    mockGetSession.mockResolvedValue({
      data: { session: { user: fakeUser } },
    });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.user).toEqual(fakeUser);
  });

  it("updates user on auth state change", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const newUser = { id: "u2", email: "new@example.com" };
    act(() => {
      authChangeCallback("SIGNED_IN", { user: newUser });
    });

    expect(result.current.user).toEqual(newUser);
  });

  it("clears user on sign out auth state change", async () => {
    const fakeUser = { id: "u1", email: "test@example.com" };
    mockGetSession.mockResolvedValue({
      data: { session: { user: fakeUser } },
    });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.user).toEqual(fakeUser);
    });

    act(() => {
      authChangeCallback("SIGNED_OUT", null);
    });

    expect(result.current.user).toBeNull();
  });

  it("unsubscribes on unmount", async () => {
    const { unmount } = renderHook(() => useAuth(), {
      wrapper: createWrapper,
    });

    await waitFor(() => {
      expect(mockOnAuthStateChange).toHaveBeenCalled();
    });

    unmount();
    expect(mockUnsubscribe).toHaveBeenCalled();
  });

  it("signIn calls supabase signInWithPassword", async () => {
    mockSignInWithPassword.mockResolvedValue({ error: null });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.signIn("test@example.com", "password123");
    });

    expect(mockSignInWithPassword).toHaveBeenCalledWith({
      email: "test@example.com",
      password: "password123", // NOSONAR typescript:S2068
    });
  });

  it("signIn throws when supabase returns error", async () => {
    const authError = new Error("Invalid credentials");
    mockSignInWithPassword.mockResolvedValue({ error: authError });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await expect(
      act(async () => {
        await result.current.signIn("bad@example.com", "wrong");
      }),
    ).rejects.toThrow("Invalid credentials");
  });

  it("signUp with email calls supabase signUp with metadata", async () => {
    mockSignUp.mockResolvedValue({ error: null });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.signUp("test@example.com", "password123", {
        name: "John Doe",
      });
    });

    expect(mockSignUp).toHaveBeenCalledWith({
      email: "test@example.com",
      password: "password123", // NOSONAR typescript:S2068
      options: {
        data: {
          first_name: "John",
          last_name: "Doe",
          full_name: "John Doe",
          display_name: "John Doe",
        },
        emailRedirectTo: "http://localhost/auth/confirm-email",
      },
    });
  });

  it("signUp without a name still sends email metadata", async () => {
    mockSignUp.mockResolvedValue({ error: null });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.signUp("jane@example.com", "password123");
    });

    expect(mockSignUp).toHaveBeenCalledWith({
      email: "jane@example.com",
      password: "password123", // NOSONAR typescript:S2068 — test value, not a real credential
      options: {
        data: {
          first_name: "",
          last_name: "",
          full_name: "",
          display_name: "",
        },
        emailRedirectTo: "http://localhost/auth/confirm-email",
      },
    });
  });

  it("signUp carries next into the confirmation redirect", async () => {
    mockSignUp.mockResolvedValue({ error: null });
    window.history.pushState({}, "", "/auth/signup?next=/options");

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.signUp("leap@example.com", "password123", {
        name: "Leap User",
      });
    });

    expect(mockSignUp).toHaveBeenCalledWith(
      expect.objectContaining({
        options: expect.objectContaining({
          emailRedirectTo: "http://localhost/auth/confirm-email?next=/options",
        }),
      }),
    );
    window.history.pushState({}, "", "/");
  });

  it("signUp carries resume_checkout next into the confirmation redirect", async () => {
    mockSignUp.mockResolvedValue({ error: null });
    const resume = "/dashboard?resume_checkout=1";
    window.history.pushState(
      {},
      "",
      `/auth/signup?next=${encodeURIComponent(resume)}`,
    );

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.signUp("leap@example.com", "password123", {
        name: "Leap User",
      });
    });

    expect(mockSignUp).toHaveBeenCalledWith(
      expect.objectContaining({
        options: expect.objectContaining({
          emailRedirectTo: `http://localhost/auth/confirm-email?next=${resume}`,
        }),
      }),
    );
    window.history.pushState({}, "", "/");
  });

  it("signUp throws when supabase returns error", async () => {
    const authError = new Error("Email already registered");
    mockSignUp.mockResolvedValue({ error: authError });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await expect(
      act(async () => {
        await result.current.signUp("dup@example.com", "password123", {
          name: "A B",
        });
      }),
    ).rejects.toThrow("Email already registered");
  });

  it("signOut calls supabase signOut", async () => {
    mockSignOut.mockResolvedValue({ error: null });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.signOut();
    });

    expect(mockSignOut).toHaveBeenCalled();
  });

  it("signOut throws when supabase returns error", async () => {
    const authError = new Error("Sign out failed");
    mockSignOut.mockResolvedValue({ error: authError });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await expect(
      act(async () => {
        await result.current.signOut();
      }),
    ).rejects.toThrow("Sign out failed");
  });

  it("clears local runs when logout returns 403", async () => {
    const forbidden = Object.assign(new Error("Forbidden"), { status: 403 });
    mockSignOut.mockResolvedValue({ error: forbidden });
    sessionStorage.setItem("optionstaxhub-analysis", "{\"analysis_id\":\"run-1\"}");
    sessionStorage.setItem("optionstaxhub-analysis-owner", "user-a");
    localStorage.setItem("optionstaxhub-packet-resume", "{\"analysisId\":\"run-1\"}");
    localStorage.setItem("appWasInstalled", "true");

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await expect(
      act(async () => {
        await result.current.signOut();
      }),
    ).rejects.toThrow("Forbidden");

    expect(sessionStorage.getItem("optionstaxhub-analysis")).toBeNull();
    expect(sessionStorage.getItem("optionstaxhub-analysis-owner")).toBeNull();
    expect(localStorage.getItem("optionstaxhub-packet-resume")).toBeNull();
    expect(localStorage.getItem("appWasInstalled")).toBe("true");
  });

  it("clears local runs when logout throws", async () => {
    mockSignOut.mockRejectedValue(new Error("network down"));
    sessionStorage.setItem("optionstaxhub-analysis", "{\"analysis_id\":\"run-1\"}");
    localStorage.setItem("custom-packet-resume", "1");

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await expect(
      act(async () => {
        await result.current.signOut();
      }),
    ).rejects.toThrow("network down");

    expect(sessionStorage.getItem("optionstaxhub-analysis")).toBeNull();
    expect(localStorage.getItem("custom-packet-resume")).toBeNull();
  });

  it("useAuth throws if used outside AuthProvider", () => {
    // Suppress console.error for expected error
    const spy = jest.spyOn(console, "error").mockImplementation(() => {});

    expect(() => {
      renderHook(() => useAuth());
    }).toThrow("useAuth must be used within AuthProvider");

    spy.mockRestore();
  });

  it("handles getSession returning no session gracefully", async () => {
    // When getSession returns no session, user should remain null
    mockGetSession.mockResolvedValue({
      data: { session: null },
    });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.user).toBeNull();
  });

  it("sets user to null when auth state changes with undefined session", async () => {
    const fakeUser = { id: "u1", email: "test@example.com" };
    mockGetSession.mockResolvedValue({
      data: { session: { user: fakeUser } },
    });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.user).toEqual(fakeUser);
    });

    // Fire onAuthStateChange with undefined session to exercise session?.user || null
    act(() => {
      authChangeCallback("TOKEN_REFRESHED", undefined);
    });

    expect(result.current.user).toBeNull();
  });
});
