export const TAX_PATH = "/dashboard";
export const OPTIONS_PATH = "/options";
export const OPTIONS_SIGN_IN_HREF = `/auth/signin?next=${OPTIONS_PATH}`;

const ALLOWED_RETURN_PATHS = new Set<string>([TAX_PATH, OPTIONS_PATH]);

/** Only Tax and Options are safe post-sign-in returns. */
export function safeNextPath(raw: string | null | undefined): string {
  if (!raw) {
    return TAX_PATH;
  }
  const path = raw.split("?")[0]?.split("#")[0] ?? "";
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
    return TAX_PATH;
  }
  return ALLOWED_RETURN_PATHS.has(path) ? path : TAX_PATH;
}
