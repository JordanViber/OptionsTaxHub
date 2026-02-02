import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import Home from "../../app/page";

const mockPush = jest.fn();
const mockUseAuth = jest.fn();
const mockUseUploadPortfolio = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
}));

jest.mock("../../app/components/InstallPrompt", () => () => null);
jest.mock("../../app/components/ServiceWorkerRegistration", () => () => null);

jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockUseAuth(),
}));

jest.mock("../../lib/api", () => ({
  useUploadPortfolio: () => mockUseUploadPortfolio(),
}));

// Helper function to create default auth mock
const createAuthMock = (
  user: any = null,
  loading: boolean = false,
  signOut?: any,
) => ({
  user,
  loading,
  signOut: signOut || jest.fn(),
});

// Helper function to create default upload portfolio mock
const createUploadMock = (overrides: any = {}) => ({
  mutate: jest.fn(),
  isPending: false,
  error: null,
  data: null,
  ...overrides,
});

// Helper function to setup mocks
const setupMocks = (auth: any = {}, upload: any = {}) => {
  mockUseAuth.mockReturnValue(createAuthMock(undefined, undefined, undefined));
  mockUseUploadPortfolio.mockReturnValue(createUploadMock());
  if (Object.keys(auth).length) {
    mockUseAuth.mockReturnValue(auth);
  }
  if (Object.keys(upload).length) {
    mockUseUploadPortfolio.mockReturnValue(upload);
  }
};

describe("Home page", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockUseAuth.mockReset();
    mockUseUploadPortfolio.mockReset();
  });

  it("renders loading state when auth is loading", () => {
    mockUseAuth.mockReturnValue({
      user: null,
      loading: true,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      error: null,
      data: null,
    });

    render(<Home />);

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("redirects to sign-in when unauthenticated", () => {
    mockUseAuth.mockReturnValue({
      user: null,
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      error: null,
      data: null,
    });

    render(<Home />);

    expect(mockPush).toHaveBeenCalledWith("/auth/signin");
  });

  it("uses display_name when available", () => {
    mockUseAuth.mockReturnValue({
      user: {
        email: "test@example.com",
        user_metadata: {
          display_name: "Display Name",
          first_name: "First",
          last_name: "Last",
        },
      },
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      error: null,
      data: null,
    });

    render(<Home />);

    expect(screen.getByText("Display Name")).toBeInTheDocument();
  });

  it("falls back to full name when display_name is missing", () => {
    mockUseAuth.mockReturnValue({
      user: {
        email: "test@example.com",
        user_metadata: {
          first_name: "First",
          last_name: "Last",
        },
      },
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      error: null,
      data: null,
    });

    render(<Home />);

    expect(screen.getByText("First Last")).toBeInTheDocument();
  });

  it("uses full_name when available in metadata", () => {
    mockUseAuth.mockReturnValue({
      user: {
        email: "test@example.com",
        user_metadata: {
          full_name: "Full Name",
        },
      },
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      error: null,
      data: null,
    });

    render(<Home />);

    expect(screen.getByText("Full Name")).toBeInTheDocument();
  });

  it("falls back to email when profile names are missing", () => {
    mockUseAuth.mockReturnValue({
      user: {
        email: "email-only@example.com",
        user_metadata: {},
      },
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      error: null,
      data: null,
    });

    render(<Home />);

    expect(screen.getByText("email-only@example.com")).toBeInTheDocument();
  });

  it("handles missing user metadata gracefully", () => {
    mockUseAuth.mockReturnValue({
      user: {
        email: "metadata-missing@example.com",
      },
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      error: null,
      data: null,
    });

    render(<Home />);

    expect(
      screen.getByText("metadata-missing@example.com"),
    ).toBeInTheDocument();
  });

  it("falls back to Account when email is missing", () => {
    mockUseAuth.mockReturnValue({
      user: {
        user_metadata: {},
      },
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      error: null,
      data: null,
    });

    render(<Home />);

    expect(screen.getByText("Account")).toBeInTheDocument();
  });

  it("triggers file input click when upload button is pressed", () => {
    mockUseAuth.mockReturnValue({
      user: {
        email: "test@example.com",
        user_metadata: { display_name: "Test User" },
      },
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      error: null,
      data: null,
    });

    const { container } = render(<Home />);
    const uploadButton = screen.getByRole("button", { name: /upload csv/i });
    const fileInput = container.querySelector('input[type="file"]');

    expect(fileInput).toBeTruthy();
    if (!fileInput) return;

    const clickSpy = jest.spyOn(fileInput, "click");

    fireEvent.click(uploadButton);

    expect(clickSpy).toHaveBeenCalled();
  });

  it("uploads file when file input changes", () => {
    const mutate = jest.fn();
    mockUseAuth.mockReturnValue({
      user: {
        email: "test@example.com",
        user_metadata: { display_name: "Test User" },
      },
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate,
      isPending: false,
      error: null,
      data: null,
    });

    const { container } = render(<Home />);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;

    const file = new File(["content"], "test.csv", { type: "text/csv" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(mutate).toHaveBeenCalledWith(file);
  });

  it("does not upload when no file is selected", () => {
    const mutate = jest.fn();
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        jest.fn(),
      ),
      createUploadMock({ mutate }),
    );

    const { container } = render(<Home />);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;

    fireEvent.change(fileInput, { target: { files: [] } });

    expect(mutate).not.toHaveBeenCalled();
  });

  it("does not upload when file list is undefined", () => {
    const mutate = jest.fn();
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        jest.fn(),
      ),
      createUploadMock({ mutate }),
    );

    const { container } = render(<Home />);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;

    fireEvent.change(fileInput, { target: {} });

    expect(mutate).not.toHaveBeenCalled();
  });

  it("does not upload when file list is null", () => {
    const mutate = jest.fn();
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        jest.fn(),
      ),
      createUploadMock({ mutate }),
    );

    const { container } = render(<Home />);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;

    fireEvent.change(fileInput, { target: { files: null } });

    expect(mutate).not.toHaveBeenCalled();
  });

  it("shows uploading state when mutation is pending", () => {
    mockUseAuth.mockReturnValue({
      user: {
        email: "test@example.com",
        user_metadata: { display_name: "Test User" },
      },
      loading: false,
      signOut: jest.fn(),
    });
    mockUseUploadPortfolio.mockReturnValue({
      mutate: jest.fn(),
      isPending: true,
      error: null,
      data: null,
    });

    render(<Home />);

    expect(screen.getByRole("button", { name: /uploading/i })).toBeDisabled();
  });

  it("renders error state when upload fails", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        jest.fn(),
      ),
      createUploadMock({ error: new Error("Upload failed") }),
    );

    render(<Home />);

    expect(screen.getByText("Upload Failed")).toBeInTheDocument();
    expect(screen.getByText("Upload failed")).toBeInTheDocument();
  });

  it("renders generic error message when error is not an Error", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        jest.fn(),
      ),
      createUploadMock({ error: "Upload failed" }),
    );

    render(<Home />);

    expect(screen.getByText("An error occurred")).toBeInTheDocument();
  });

  it("renders portfolio data table when upload succeeds", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        jest.fn(),
      ),
      createUploadMock({
        data: [
          { symbol: "AAPL", qty: 10, price: 150 },
          { symbol: "MSFT", qty: 5, price: 310 },
        ],
      }),
    );

    render(<Home />);

    expect(
      screen.getByText("Portfolio Data (First 5 Rows)"),
    ).toBeInTheDocument();
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("MSFT")).toBeInTheDocument();
    expect(screen.getByText("150.00")).toBeInTheDocument();
  });

  it("signs out and redirects when menu action is clicked", async () => {
    const signOut = jest.fn(() => Promise.resolve());
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        signOut,
      ),
      createUploadMock(),
    );

    render(<Home />);

    fireEvent.click(screen.getByText("Test User"));

    await waitFor(() => {
      expect(screen.getByText("Sign Out")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Sign Out"));

    await waitFor(() => {
      expect(signOut).toHaveBeenCalled();
      expect(mockPush).toHaveBeenCalledWith("/auth/signin");
    });
  });

  it("shows menu without email when user email is missing", async () => {
    setupMocks(
      createAuthMock(
        { user_metadata: { display_name: "No Email" } },
        false,
        jest.fn(),
      ),
      createUploadMock(),
    );

    render(<Home />);

    fireEvent.click(screen.getByText("No Email"));

    await waitFor(() => {
      expect(screen.getAllByText("No Email").length).toBeGreaterThan(1);
    });

    const menuLabel = screen.getAllByText("No Email")[1];
    expect(menuLabel.textContent).toBe("No Email");
  });

  it("closes the error alert when close button is clicked", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        jest.fn(),
      ),
      createUploadMock({ error: new Error("Upload failed") }),
    );

    render(<Home />);

    const closeButton = screen.getByLabelText("Close");
    fireEvent.click(closeButton);
  });

  it("closes the menu on backdrop click", async () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        jest.fn(),
      ),
      createUploadMock(),
    );

    render(<Home />);

    fireEvent.click(screen.getByText("Test User"));

    await waitFor(() => {
      expect(screen.getByText("Sign Out")).toBeInTheDocument();
    });

    const backdrop = document.querySelector(".MuiBackdrop-root");
    if (backdrop) {
      fireEvent.click(backdrop);
    }

    await waitFor(() => {
      expect(screen.queryByText("Sign Out")).not.toBeInTheDocument();
    });
  });
});
