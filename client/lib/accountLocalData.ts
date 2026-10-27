import { readPacketResumeExpected } from "@/lib/packetResumeLocal";
import {
  PACKET_CHECKOUT_STARTED_KEY,
  PACKET_RESUME_ANALYSIS_KEY,
  PACKET_RESUME_CHECKOUT_KEY,
  PACKET_RESUME_LOCAL_KEY,
  PACKET_RESUME_SAMPLE_KEY,
} from "@/lib/safeNextPath";

export const ANALYSIS_STORAGE_KEY = "optionstaxhub-analysis";
export const ANALYSIS_OWNER_STORAGE_KEY = "optionstaxhub-analysis-owner";
/** Stored owner for a run created while signed out. */
export const GUEST_ANALYSIS_OWNER = "guest";

const PACKET_CHECKOUT_INFLIGHT_KEY = "optionstaxhub-packet-checkout-inflight";
const OWNER_TOKEN = /^[A-Za-z0-9_-]{1,128}$/;

const ALWAYS_REMOVE = [
  ANALYSIS_STORAGE_KEY,
  ANALYSIS_OWNER_STORAGE_KEY,
  PACKET_RESUME_CHECKOUT_KEY,
  PACKET_RESUME_SAMPLE_KEY,
  PACKET_RESUME_ANALYSIS_KEY,
  PACKET_RESUME_LOCAL_KEY,
  PACKET_CHECKOUT_STARTED_KEY,
  PACKET_CHECKOUT_INFLIGHT_KEY,
];

function isAccountDataKey(key: string): boolean {
  return (
    key === ANALYSIS_STORAGE_KEY ||
    key.startsWith("optionstaxhub-") ||
    key.includes("packet-resume")
  );
}

function scrubStorage(storage: Storage): void {
  const keysToRemove = ALWAYS_REMOVE.slice();
  try {
    const length = storage.length;
    if (typeof length === "number" && typeof storage.key === "function") {
      for (let index = 0; index < length; index += 1) {
        const key = storage.key(index);
        if (key && isAccountDataKey(key) && !keysToRemove.includes(key)) {
          keysToRemove.push(key);
        }
      }
    }
  } catch {
    // A blocked enumeration still removes the known keys below.
  }
  for (let index = 0; index < keysToRemove.length; index += 1) {
    try {
      storage.removeItem(keysToRemove[index]);
    } catch {
      // ignore quota / private-mode failures
    }
  }
}

function scrubGlobalStorage(readStorage: () => Storage): void {
  try {
    scrubStorage(readStorage());
  } catch {
    // The storage getter itself can throw SecurityError.
  }
}

/**
 * Drop every locally stored run, analysis, and packet-resume claim.
 * Safe to call more than once, and before or after a failed logout.
 */
export function clearAccountLocalData(): void {
  scrubGlobalStorage(() => sessionStorage);
  scrubGlobalStorage(() => localStorage);
}

export function clearStoredAnalysis(): void {
  try {
    sessionStorage.removeItem(ANALYSIS_STORAGE_KEY);
  } catch {
    // ignore quota / private-mode failures
  }
  try {
    sessionStorage.removeItem(ANALYSIS_OWNER_STORAGE_KEY);
  } catch {
    // ignore quota / private-mode failures
  }
}

function untaggedCheckoutClaim(): boolean {
  try {
    if (sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) !== "1") {
      return false;
    }
    const expected = readPacketResumeExpected();
    if (!expected) {
      return false;
    }
    const raw = sessionStorage.getItem(ANALYSIS_STORAGE_KEY);
    if (!raw) {
      return false;
    }
    const analysis = JSON.parse(raw) as {
      analysis_id?: unknown;
      tax_profile?: { tax_year?: unknown };
    };
    return (
      analysis.analysis_id === expected.analysisId &&
      analysis.tax_profile?.tax_year === expected.taxYear
    );
  } catch {
    return false;
  }
}

function discardUntaggedStoredAnalysis(): void {
  try {
    if (!sessionStorage.getItem(ANALYSIS_STORAGE_KEY)) {
      return;
    }
    if (readStoredAnalysisOwner() !== undefined) {
      return;
    }
    if (untaggedCheckoutClaim()) {
      return;
    }
    clearStoredAnalysis();
  } catch {
    // ignore quota / private-mode failures
  }
}

export function writeStoredAnalysis(
  serialized: string,
  ownerId: string | null,
): void {
  sessionStorage.setItem(ANALYSIS_STORAGE_KEY, serialized);
  sessionStorage.setItem(
    ANALYSIS_OWNER_STORAGE_KEY,
    ownerId ?? GUEST_ANALYSIS_OWNER,
  );
}

/**
 * `undefined` means the run has no usable owner.
 * `null` means a guest created it.
 */
export function readStoredAnalysisOwner(): string | null | undefined {
  const raw = sessionStorage.getItem(ANALYSIS_OWNER_STORAGE_KEY);
  if (raw == null) {
    return undefined;
  }
  if (raw === GUEST_ANALYSIS_OWNER) {
    return null;
  }
  if (!OWNER_TOKEN.test(raw)) {
    return undefined;
  }
  return raw;
}

export function ownerVisibleTo(
  owner: string | null | undefined,
  viewerId: string | null,
): boolean {
  if (owner === undefined) {
    return false;
  }
  if (owner === null) {
    return viewerId === null;
  }
  return owner === viewerId;
}

export function storedAnalysisVisibleTo(viewerId: string | null): boolean {
  discardUntaggedStoredAnalysis();
  try {
    const raw = sessionStorage.getItem(ANALYSIS_STORAGE_KEY);
    if (!raw) {
      return false;
    }
    return ownerVisibleTo(readStoredAnalysisOwner(), viewerId);
  } catch {
    return false;
  }
}

export function readStoredAnalysisJson(): unknown | null {
  discardUntaggedStoredAnalysis();
  const raw = sessionStorage.getItem(ANALYSIS_STORAGE_KEY);
  if (!raw) {
    return null;
  }
  return JSON.parse(raw) as unknown;
}
