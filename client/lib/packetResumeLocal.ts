import {
  PACKET_RESUME_CHECKOUT_KEY,
  PACKET_RESUME_LOCAL_KEY,
  PACKET_RESUME_LOCAL_TTL_MS,
  PACKET_RESUME_LOCAL_VERSION,
  PACKET_RESUME_SAMPLE_KEY,
  RESUME_CHECKOUT_PARAM,
  TAX_PATH,
} from "@/lib/safeNextPath";

export const PACKET_RESUME_REUPLOAD_COPY =
  "Re-upload your CSV to continue to checkout.";

const STORED_ANALYSIS_KEY = "optionstaxhub-analysis";

/** Survives a strict-mode remount after the localStorage key is consumed. */
let uploadedResumeNeedsCsv = false;

/** Tests only. */
export function resetPacketResumeLocalForTests(): void {
  uploadedResumeNeedsCsv = false;
}

export function clearPacketResumeLocal(): void {
  try {
    localStorage.removeItem(PACKET_RESUME_LOCAL_KEY);
  } catch {
    // ignore quota / private-mode failures
  }
}

/** Guest Pay handoff. Only version, sample flag, and expiry. */
export function rememberPacketResumeLocal(
  sample: boolean,
  now = Date.now(),
): void {
  try {
    const payload = {
      v: PACKET_RESUME_LOCAL_VERSION,
      sample: sample === true,
      exp: now + PACKET_RESUME_LOCAL_TTL_MS,
    };
    localStorage.setItem(PACKET_RESUME_LOCAL_KEY, JSON.stringify(payload));
  } catch {
    // ignore quota / private-mode failures
  }
}

function isResumeCheckoutPage(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    const url = new URL(window.location.href);
    return (
      url.pathname === TAX_PATH &&
      url.searchParams.get(RESUME_CHECKOUT_PARAM) === "1"
    );
  } catch {
    return false;
  }
}

function parsePacketResumeLocal(
  raw: string,
  now: number,
): { sample: boolean } | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (record.v !== PACKET_RESUME_LOCAL_VERSION) {
    return null;
  }
  if (typeof record.sample !== "boolean") {
    return null;
  }
  if (
    typeof record.exp !== "number" ||
    !Number.isFinite(record.exp) ||
    record.exp <= now
  ) {
    return null;
  }
  return { sample: record.sample };
}

/**
 * Copy a valid cross-tab handoff into the session markers and delete it.
 * Returns the sample flag when markers were restored.
 */
function restorePacketResumeMarkers(now: number): { sample: boolean } | null {
  try {
    if (sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) === "1") {
      return null;
    }
  } catch {
    return null;
  }
  let raw: string | null;
  try {
    raw = localStorage.getItem(PACKET_RESUME_LOCAL_KEY);
  } catch {
    return null;
  }
  if (raw == null) {
    return null;
  }
  const parsed = parsePacketResumeLocal(raw, now);
  if (!parsed) {
    clearPacketResumeLocal();
    return null;
  }
  try {
    sessionStorage.setItem(PACKET_RESUME_CHECKOUT_KEY, "1");
    if (parsed.sample) {
      sessionStorage.setItem(PACKET_RESUME_SAMPLE_KEY, "1");
    }
  } catch {
    return null;
  }
  clearPacketResumeLocal();
  return parsed;
}

function hasStoredAnalysis(): boolean {
  try {
    return Boolean(sessionStorage.getItem(STORED_ANALYSIS_KEY));
  } catch {
    return true;
  }
}

function clearRestoredResumeMarkers(): void {
  try {
    sessionStorage.removeItem(PACKET_RESUME_CHECKOUT_KEY);
    sessionStorage.removeItem(PACKET_RESUME_SAMPLE_KEY);
  } catch {
    // ignore
  }
}

/**
 * Run during dashboard render, before resume effects read sessionStorage.
 * Returns true when an uploaded run cannot continue and the page should
 * ask for the CSV again. Sample runs are left on the existing re-run path.
 */
export function consumePacketResumeLocalHandoff(now = Date.now()): boolean {
  if (!isResumeCheckoutPage()) {
    return false;
  }
  if (uploadedResumeNeedsCsv) {
    return true;
  }
  const restored = restorePacketResumeMarkers(now);
  if (!restored || restored.sample) {
    return false;
  }
  if (hasStoredAnalysis()) {
    return false;
  }
  clearRestoredResumeMarkers();
  uploadedResumeNeedsCsv = true;
  return true;
}
