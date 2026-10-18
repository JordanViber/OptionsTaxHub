import {
  DASHBOARD_RESUME_CHECKOUT_PATH,
  OPTIONS_SIGN_IN_HREF,
  safeNextPath,
  signInHref,
  signUpHref,
} from "../../lib/safeNextPath";

describe("safeNextPath", () => {
  it("allows known pages and rejects everything else", () => {
    expect(safeNextPath(null)).toBe("/dashboard");
    expect(safeNextPath("")).toBe("/dashboard");
    expect(safeNextPath(undefined)).toBe("/dashboard");
    expect(safeNextPath(1 as unknown as string)).toBe("/dashboard");
    expect(safeNextPath("/")).toBe("/");
    expect(safeNextPath("/options")).toBe("/options");
    expect(safeNextPath("/dashboard")).toBe("/dashboard");
    expect(safeNextPath("/settings")).toBe("/settings");
    expect(safeNextPath("/privacy")).toBe("/privacy");
    expect(safeNextPath("/options?x=1")).toBe("/options");
    expect(safeNextPath("//evil.example")).toBe("/dashboard");
    expect(safeNextPath("/auth/signin")).toBe("/dashboard");
    expect(safeNextPath("https://evil.example")).toBe("/dashboard");
    expect(safeNextPath("/this-route-does-not-exist")).toBe("/dashboard");
    expect(safeNextPath("/\\dashboard")).toBe("/dashboard");
    expect(OPTIONS_SIGN_IN_HREF).toBe("/auth/signin?next=/options");
    expect(signInHref("/options", "runs")).toBe(
      "/auth/signin?next=/options&reason=runs",
    );
    expect(signInHref("/")).toBe("/auth/signin?next=/");
    expect(signUpHref("/dashboard")).toBe("/auth/signup?next=/dashboard");
  });

  it("keeps resume_checkout=1 only on /dashboard", () => {
    expect(safeNextPath("/dashboard?resume_checkout=1")).toBe(
      DASHBOARD_RESUME_CHECKOUT_PATH,
    );
    expect(safeNextPath("/dashboard?resume_checkout=1#pay")).toBe(
      DASHBOARD_RESUME_CHECKOUT_PATH,
    );
    expect(
      safeNextPath("/dashboard?resume_checkout=1&extra=1"),
    ).toBe(DASHBOARD_RESUME_CHECKOUT_PATH);
    expect(
      safeNextPath("/dashboard?resume_checkout=1&next=https://evil.example"),
    ).toBe("/dashboard");
    expect(safeNextPath("/dashboard?resume_checkout=2")).toBe("/dashboard");
    expect(safeNextPath("/dashboard?resume_checkout=")).toBe("/dashboard");
    expect(safeNextPath("/options?resume_checkout=1")).toBe("/options");
    expect(safeNextPath("/options?x=1")).toBe("/options");
    expect(safeNextPath("//evil.example")).toBe("/dashboard");
    expect(safeNextPath("https://evil.example")).toBe("/dashboard");
    expect(safeNextPath("/\\dashboard")).toBe("/dashboard");
    expect(safeNextPath("/auth/signin")).toBe("/dashboard");
    expect(safeNextPath("/dashboard/resume-checkout")).toBe("/dashboard");
  });

  it("round-trips the resume path through sign-in and sign-up hrefs", () => {
    const signIn = signInHref(DASHBOARD_RESUME_CHECKOUT_PATH);
    const signInNext = new URL(signIn, "https://optionstaxhub.invalid").searchParams.get(
      "next",
    );
    expect(safeNextPath(signInNext)).toBe(DASHBOARD_RESUME_CHECKOUT_PATH);
    expect(signIn).toBe(
      `/auth/signin?next=${encodeURIComponent(DASHBOARD_RESUME_CHECKOUT_PATH)}`,
    );

    const signUp = signUpHref(signInNext);
    const signUpNext = new URL(signUp, "https://optionstaxhub.invalid").searchParams.get(
      "next",
    );
    expect(safeNextPath(signUpNext)).toBe(DASHBOARD_RESUME_CHECKOUT_PATH);
    expect(signInHref("/options", "runs")).toBe(
      "/auth/signin?next=/options&reason=runs",
    );
    expect(signUpHref("/dashboard")).toBe("/auth/signup?next=/dashboard");
  });
});
