import "@testing-library/jest-dom";

// Mock window.matchMedia
Object.defineProperty(window, "matchMedia", {
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

// Mock Supabase
jest.mock("@/lib/supabase", () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: jest.fn(() =>
        Promise.resolve({
          data: {
            session: {
              user: {
                id: "test-user-id",
                email: "test@example.com",
                user_metadata: {
                  first_name: "Test",
                  last_name: "User",
                  display_name: "Test User",
                },
              },
            },
          },
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
    },
  }),
}));
