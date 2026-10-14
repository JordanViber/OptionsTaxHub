export const HOME_PATH = "/";
export const TAX_PATH = "/dashboard";
export const OPTIONS_PATH = "/options";
export const SETTINGS_PATH = "/settings";
export const PRIVACY_PATH = "/privacy";
export const OPTIONS_SIGN_IN_HREF = `/auth/signin?next=${OPTIONS_PATH}`;

export const UPLOAD_INTENT_KEY = "oth-upload-intent";
export const LOAD_SAMPLE_KEY = "oth-load-sample";
export const OPEN_RUNS_KEY = "oth-open-runs";

const ALLOWED_RETURN_PATHS = new Set<string>([
  HOME_PATH,
  TAX_PATH,
  OPTIONS_PATH,
  SETTINGS_PATH,
  PRIVACY_PATH,
]);

/** Only known in-app paths are safe post-sign-in returns. */
export function safeNextPath(raw: string | null | undefined): string {
  if (!raw) {
    return TAX_PATH;
  }
  const path = raw.split("?")[0]?.split("#")[0] ?? "";
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
    return TAX_PATH;
  }
  if (path.includes("://")) {
    return TAX_PATH;
  }
  return ALLOWED_RETURN_PATHS.has(path) ? path : TAX_PATH;
}

export function signInHref(
  from: string | null | undefined,
  reason?: "runs" | "profile",
): string {
  const next = safeNextPath(from);
  const reasonPart = reason ? `&reason=${reason}` : "";
  return `/auth/signin?next=${next}${reasonPart}`;
}

export function signUpHref(from: string | null | undefined): string {
  return `/auth/signup?next=${safeNextPath(from)}`;
}

export function rememberUploadIntent(): void {
  try {
    sessionStorage.setItem(UPLOAD_INTENT_KEY, "1");
  } catch {
    // ignore
  }
}

export function rememberSampleIntent(): void {
  try {
    sessionStorage.setItem(LOAD_SAMPLE_KEY, "1");
  } catch {
    // ignore
  }
}

export function rememberOpenRuns(): void {
  try {
    sessionStorage.setItem(OPEN_RUNS_KEY, "1");
  } catch {
    // ignore
  }
}
