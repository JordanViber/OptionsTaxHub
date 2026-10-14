import {
  OPTIONS_SIGN_IN_HREF,
  safeNextPath,
  signInHref,
  signUpHref,
} from "../../lib/safeNextPath";

describe("safeNextPath", () => {
  it("allows known pages and rejects everything else", () => {
    expect(safeNextPath(null)).toBe("/dashboard");
    expect(safeNextPath("")).toBe("/dashboard");
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
    expect(OPTIONS_SIGN_IN_HREF).toBe("/auth/signin?next=/options");
    expect(signInHref("/options", "runs")).toBe(
      "/auth/signin?next=/options&reason=runs",
    );
    expect(signInHref("/")).toBe("/auth/signin?next=/");
    expect(signUpHref("/dashboard")).toBe("/auth/signup?next=/dashboard");
  });
});
