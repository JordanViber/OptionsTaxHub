import { OPTIONS_SIGN_IN_HREF, safeNextPath } from "../../lib/safeNextPath";

describe("safeNextPath", () => {
  it("allows Tax and Options and rejects everything else", () => {
    expect(safeNextPath(null)).toBe("/dashboard");
    expect(safeNextPath("")).toBe("/dashboard");
    expect(safeNextPath("/options")).toBe("/options");
    expect(safeNextPath("/dashboard")).toBe("/dashboard");
    expect(safeNextPath("/options?x=1")).toBe("/options");
    expect(safeNextPath("//evil.example")).toBe("/dashboard");
    expect(safeNextPath("/auth/signin")).toBe("/dashboard");
    expect(safeNextPath("https://evil.example")).toBe("/dashboard");
    expect(OPTIONS_SIGN_IN_HREF).toBe("/auth/signin?next=/options");
  });
});
