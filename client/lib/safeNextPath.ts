export const HOME_PATH = "/";
export const TAX_PATH = "/dashboard";
export const OPTIONS_PATH = "/options";
export const SETTINGS_PATH = "/settings";
export const PRIVACY_PATH = "/privacy";
export const OPTIONS_SIGN_IN_HREF = `/auth/signin?next=${OPTIONS_PATH}`;

export const UPLOAD_INTENT_KEY = "oth-upload-intent";
export const LOAD_SAMPLE_KEY = "oth-load-sample";
export const OPEN_RUNS_KEY = "oth-open-runs";
export const RESUME_CHECKOUT_PARAM = "resume_checkout";
export const PACKET_RESUME_CHECKOUT_KEY = "optionstaxhub-packet-resume-checkout";
export const PACKET_RESUME_SAMPLE_KEY = "optionstaxhub-packet-resume-sample";
export const DASHBOARD_RESUME_CHECKOUT_PATH = `${TAX_PATH}?${RESUME_CHECKOUT_PARAM}=1`;

const ALLOWED_RETURN_PATHS = new Set<string>([
  HOME_PATH,
  TAX_PATH,
  OPTIONS_PATH,
  SETTINGS_PATH,
  PRIVACY_PATH,
]);

const SAFE_NEXT_ORIGIN = "https://optionstaxhub.invalid";

/** Encode next only when a query marker would split or truncate the href. */
export function nextParam(path: string): string {
  return /[?&#%]/.test(path) ? encodeURIComponent(path) : path;
}

/** Only known in-app paths are safe post-sign-in returns. */
export function safeNextPath(raw: string | null | undefined): string {
  if (!raw || typeof raw !== "string") {
    return TAX_PATH;
  }
  if (
    !raw.startsWith("/") ||
    raw.startsWith("//") ||
    raw.includes("\\") ||
    raw.includes("://")
  ) {
    return TAX_PATH;
  }
  let url: URL;
  try {
    url = new URL(raw, SAFE_NEXT_ORIGIN);
  } catch {
    return TAX_PATH;
  }
  if (url.origin !== SAFE_NEXT_ORIGIN) {
    return TAX_PATH;
  }
  const path = url.pathname;
  if (!ALLOWED_RETURN_PATHS.has(path)) {
    return TAX_PATH;
  }
  if (
    path === TAX_PATH &&
    url.searchParams.get(RESUME_CHECKOUT_PARAM) === "1"
  ) {
    return DASHBOARD_RESUME_CHECKOUT_PATH;
  }
  return path;
}

export function signInHref(
  from: string | null | undefined,
  reason?: "runs" | "profile",
): string {
  const next = safeNextPath(from);
  const reasonPart = reason ? `&reason=${reason}` : "";
  return `/auth/signin?next=${nextParam(next)}${reasonPart}`;
}

export function signUpHref(from: string | null | undefined): string {
  return `/auth/signup?next=${nextParam(safeNextPath(from))}`;
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
