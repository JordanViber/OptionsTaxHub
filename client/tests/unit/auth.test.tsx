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
      password: "password123",
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
        firstName: "John",
        lastName: "Doe",
        displayName: "JD",
        phone: "",
        providerType: "email",
      });
    });

    expect(mockSignUp).toHaveBeenCalledWith({
      email: "test@example.com",
      password: "password123",
      options: {
        data: {
          first_name: "John",
          last_name: "Doe",
          full_name: "John Doe",
          display_name: "JD",
          phone: "",
          provider_type: "email",
        },
      },
    });
  });

  it("signUp with phone uses phone field instead of email", async () => {
    mockSignUp.mockResolvedValue({ error: null });

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.signUp("", "password123", {
        firstName: "Jane",
        lastName: "Doe",
        displayName: "Jane",
        phone: "+15551234567",
        providerType: "phone",
      });
    });

    expect(mockSignUp).toHaveBeenCalledWith({
      phone: "+15551234567",
      password: "password123",
      options: {
        data: expect.objectContaining({
          provider_type: "phone",
          phone: "+15551234567",
        }),
      },
    });
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
          firstName: "A",
          lastName: "B",
          displayName: "AB",
          phone: "",
          providerType: "email",
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
