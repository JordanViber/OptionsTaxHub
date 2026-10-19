import {
  PACKET_CHECKOUT_STARTED_KEY,
  PACKET_CHECKOUT_STARTED_TTL_MS,
  PACKET_RESUME_ANALYSIS_KEY,
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
const CHECKOUT_STARTED_FUTURE_SKEW_MS = 60 * 1000;

export type PacketResumeHandoff = {
  sample: boolean;
  analysisId: string | null;
  taxYear: number | null;
  missingId: boolean;
};

export type PacketResumeExpected = {
  analysisId: string;
  taxYear: number;
};

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

export function clearPacketCheckoutStarted(): void {
  try {
    localStorage.removeItem(PACKET_CHECKOUT_STARTED_KEY);
  } catch {
    // ignore quota / private-mode failures
  }
}

export function clearPacketResumeSessionMarkers(): void {
  try {
    sessionStorage.removeItem(PACKET_RESUME_CHECKOUT_KEY);
    sessionStorage.removeItem(PACKET_RESUME_SAMPLE_KEY);
    sessionStorage.removeItem(PACKET_RESUME_ANALYSIS_KEY);
  } catch {
    // ignore
  }
}

export function rememberPacketResumeExpected(
  analysisId: string,
  taxYear: number,
): void {
  if (!isAnalysisId(analysisId) || !isTaxYear(taxYear)) {
    return;
  }
  try {
    sessionStorage.setItem(
      PACKET_RESUME_ANALYSIS_KEY,
      JSON.stringify({ analysisId, taxYear }),
    );
  } catch {
    // ignore quota / private-mode failures
  }
}

export function readPacketResumeExpected(): PacketResumeExpected | null {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(PACKET_RESUME_ANALYSIS_KEY);
  } catch {
    return null;
  }
  if (!raw) {
    return null;
  }
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
  if (!isAnalysisId(record.analysisId) || !isTaxYear(record.taxYear)) {
    return null;
  }
  return { analysisId: record.analysisId, taxYear: record.taxYear };
}

export function clearPacketResumeExpected(): void {
  try {
    sessionStorage.removeItem(PACKET_RESUME_ANALYSIS_KEY);
  } catch {
    // ignore
  }
}

/** Guest Pay handoff. Id string and tax year only — never a run or tax data. */
export function rememberPacketResumeLocal(input: {
  sample: boolean;
  analysisId?: string | null;
  taxYear?: number | null;
  now?: number;
}): void {
  const now = input.now ?? Date.now();
  try {
    const payload: Record<string, unknown> = {
      v: PACKET_RESUME_LOCAL_VERSION,
      sample: input.sample === true,
      exp: now + PACKET_RESUME_LOCAL_TTL_MS,
    };
    if (isAnalysisId(input.analysisId)) {
      payload.analysisId = input.analysisId;
    }
    if (isTaxYear(input.taxYear)) {
      payload.taxYear = input.taxYear;
    }
    localStorage.setItem(PACKET_RESUME_LOCAL_KEY, JSON.stringify(payload));
  } catch {
    // ignore quota / private-mode failures
  }
}

export function readPacketCheckoutStarted(): string | null {
  try {
    return localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY);
  } catch {
    return null;
  }
}

export function rememberPacketCheckoutStarted(now = Date.now()): string {
  const claim = String(now);
  try {
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, claim);
  } catch {
    // ignore quota / private-mode failures
  }
  return claim;
}

export function clearPacketCheckoutStartedIfClaim(claim: string): void {
  try {
    if (localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY) === claim) {
      localStorage.removeItem(PACKET_CHECKOUT_STARTED_KEY);
    }
  } catch {
    // ignore
  }
}

/**
 * True when a checkout claim is inside the last five minutes.
 * A value more than about a minute in the future is ignored and left in place.
 */
export function packetCheckoutStartedRecently(now = Date.now()): boolean {
  const raw = readPacketCheckoutStarted();
  if (raw == null || !/^\d+$/.test(raw)) {
    return false;
  }
  const started = Number(raw);
  if (!Number.isFinite(started)) {
    return false;
  }
  const age = now - started;
  if (age < 0) {
    return -age <= CHECKOUT_STARTED_FUTURE_SKEW_MS;
  }
  return age < PACKET_CHECKOUT_STARTED_TTL_MS;
}

export function resumeAnalysisMatches(analysis: {
  analysis_id?: string;
  sample_run?: boolean;
  tax_profile?: { tax_year?: number } | null;
}): boolean {
  const expected = readPacketResumeExpected();
  if (!expected) {
    return false;
  }
  if (analysis.tax_profile?.tax_year !== expected.taxYear) {
    return false;
  }
  if (analysis.analysis_id && analysis.analysis_id === expected.analysisId) {
    return true;
  }
  try {
    return (
      sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY) === "1" &&
      analysis.sample_run === true
    );
  } catch {
    return false;
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

function isAnalysisId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isTaxYear(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parsePacketResumeLocal(
  raw: string,
  now: number,
): PacketResumeHandoff | null {
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
  if (record.v !== 1 && record.v !== PACKET_RESUME_LOCAL_VERSION) {
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
  const analysisId = isAnalysisId(record.analysisId) ? record.analysisId : null;
  const taxYear = isTaxYear(record.taxYear) ? record.taxYear : null;
  const missingId =
    record.v !== PACKET_RESUME_LOCAL_VERSION ||
    analysisId == null ||
    taxYear == null;
  return {
    sample: record.sample,
    analysisId: missingId ? null : analysisId,
    taxYear: missingId ? null : taxYear,
    missingId,
  };
}

function removeSessionItem(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // A blocked clear must not drop a handoff that was otherwise restored.
  }
}

function writeRestoredMarkers(parsed: PacketResumeHandoff): boolean {
  try {
    sessionStorage.setItem(PACKET_RESUME_CHECKOUT_KEY, "1");
    if (parsed.sample) {
      sessionStorage.setItem(PACKET_RESUME_SAMPLE_KEY, "1");
    } else {
      removeSessionItem(PACKET_RESUME_SAMPLE_KEY);
    }
    if (
      !parsed.missingId &&
      parsed.analysisId &&
      parsed.taxYear != null
    ) {
      sessionStorage.setItem(
        PACKET_RESUME_ANALYSIS_KEY,
        JSON.stringify({
          analysisId: parsed.analysisId,
          taxYear: parsed.taxYear,
        }),
      );
    } else {
      removeSessionItem(PACKET_RESUME_ANALYSIS_KEY);
    }
  } catch {
    return false;
  }
  return true;
}

/**
 * Copy a valid cross-tab handoff into the session markers and delete it.
 * A tab that already has the checkout marker keeps its handoff, but a
 * non-sample handoff still clears a stale sample flag.
 */
function restorePacketResumeMarkers(now: number): PacketResumeHandoff | null {
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
  let checkoutAlready = false;
  try {
    checkoutAlready = sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) === "1";
  } catch {
    return null;
  }
  if (checkoutAlready) {
    if (!parsed.sample) {
      try {
        sessionStorage.removeItem(PACKET_RESUME_SAMPLE_KEY);
      } catch {
        return null;
      }
    }
    return null;
  }
  if (!writeRestoredMarkers(parsed)) {
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

/**
 * Run during dashboard render, before resume effects read sessionStorage.
 * Returns true when an uploaded run cannot continue and the page should
 * ask for the CSV again. Sample runs are left on the existing re-run path.
 * A handoff that names an analysis id stays marked so the dashboard can
 * load that saved run instead of checking out whatever is already on screen.
 */
export function consumePacketResumeLocalHandoff(now = Date.now()): boolean {
  if (!isResumeCheckoutPage()) {
    return false;
  }
  if (uploadedResumeNeedsCsv) {
    return true;
  }
  const restored = restorePacketResumeMarkers(now);
  if (!restored || restored.sample || !restored.missingId) {
    return false;
  }
  if (hasStoredAnalysis()) {
    return false;
  }
  clearPacketResumeSessionMarkers();
  uploadedResumeNeedsCsv = true;
  return true;
}
