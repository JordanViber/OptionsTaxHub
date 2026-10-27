import {
  ANALYSIS_OWNER_STORAGE_KEY,
  ANALYSIS_STORAGE_KEY,
  GUEST_ANALYSIS_OWNER,
  GUEST_UNSAVED_FILENAME_KEY,
  GUEST_UNSAVED_KEY,
  clearAccountLocalData,
  clearStoredAnalysis,
  ownerVisibleTo,
  readStoredAnalysisOwner,
  storedAnalysisVisibleTo,
  writeStoredAnalysis,
} from "../../lib/accountLocalData";

describe("accountLocalData", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("treats a guest run as different from a signed-in run", () => {
    expect(ownerVisibleTo(undefined, null)).toBe(false);
    expect(ownerVisibleTo(undefined, "user-a")).toBe(false);
    expect(ownerVisibleTo(null, null)).toBe(true);
    expect(ownerVisibleTo(null, "user-a")).toBe(false);
    expect(ownerVisibleTo("user-a", null)).toBe(false);
    expect(ownerVisibleTo("user-a", "user-a")).toBe(true);
    expect(ownerVisibleTo("user-a", "user-b")).toBe(false);
  });

  it("tags a stored run with the user who created it", () => {
    writeStoredAnalysis(JSON.stringify({ analysis_id: "run-1" }), "user-a");
    expect(sessionStorage.getItem(ANALYSIS_STORAGE_KEY)).toContain("run-1");
    expect(readStoredAnalysisOwner()).toBe("user-a");
    expect(storedAnalysisVisibleTo("user-a")).toBe(true);
    expect(storedAnalysisVisibleTo("user-b")).toBe(false);
    expect(storedAnalysisVisibleTo(null)).toBe(false);

    writeStoredAnalysis(JSON.stringify({ analysis_id: "guest-run" }), null);
    expect(sessionStorage.getItem(ANALYSIS_OWNER_STORAGE_KEY)).toBe(
      GUEST_ANALYSIS_OWNER,
    );
    expect(readStoredAnalysisOwner()).toBeNull();
    expect(storedAnalysisVisibleTo(null)).toBe(true);
    expect(storedAnalysisVisibleTo("user-a")).toBe(false);
  });

  it("drops an untagged run on read, including a junk owner token", () => {
    sessionStorage.setItem(ANALYSIS_STORAGE_KEY, "{\"analysis_id\":\"legacy\"}");
    expect(readStoredAnalysisOwner()).toBeUndefined();
    expect(storedAnalysisVisibleTo(null)).toBe(false);
    expect(sessionStorage.getItem(ANALYSIS_STORAGE_KEY)).toBeNull();

    sessionStorage.setItem(ANALYSIS_STORAGE_KEY, "{\"analysis_id\":\"legacy\"}");
    sessionStorage.setItem(ANALYSIS_OWNER_STORAGE_KEY, "{\"not\":\"an id\"}");
    expect(readStoredAnalysisOwner()).toBeUndefined();
    expect(storedAnalysisVisibleTo("user-a")).toBe(false);
    expect(sessionStorage.getItem(ANALYSIS_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem(ANALYSIS_OWNER_STORAGE_KEY)).toBeNull();
  });

  it("keeps an untagged run when this tab is claiming it at checkout", () => {
    sessionStorage.setItem(
      ANALYSIS_STORAGE_KEY,
      JSON.stringify({
        analysis_id: "legacy-claim",
        tax_profile: { tax_year: 2025 },
      }),
    );
    sessionStorage.setItem("optionstaxhub-packet-resume-checkout", "1");
    sessionStorage.setItem(
      "optionstaxhub-packet-resume-analysis",
      JSON.stringify({ analysisId: "legacy-claim", taxYear: 2025 }),
    );

    expect(storedAnalysisVisibleTo("user-a")).toBe(false);
    expect(storedAnalysisVisibleTo(null)).toBe(false);
    expect(sessionStorage.getItem(ANALYSIS_STORAGE_KEY)).toContain("legacy-claim");
  });

  it("clears analysis and packet-resume keys from both storages", () => {
    sessionStorage.setItem(ANALYSIS_STORAGE_KEY, "{\"analysis_id\":\"run-1\"}");
    sessionStorage.setItem(ANALYSIS_OWNER_STORAGE_KEY, "user-a");
    sessionStorage.setItem("optionstaxhub-packet-resume-checkout", "1");
    sessionStorage.setItem("optionstaxhub-packet-paid:run-1", "cs_test");
    sessionStorage.setItem("custom-packet-resume", "handoff");
    localStorage.setItem("optionstaxhub-packet-resume", "{\"analysisId\":\"run-1\"}");
    localStorage.setItem("optionstaxhub-packet-checkout-started", "10");
    localStorage.setItem("appWasInstalled", "true");
    sessionStorage.setItem("oth-load-sample", "1");
    sessionStorage.setItem(GUEST_UNSAVED_KEY, "1");
    sessionStorage.setItem(GUEST_UNSAVED_FILENAME_KEY, "guest.csv");
    localStorage.setItem(GUEST_UNSAVED_KEY, "1");
    localStorage.setItem(GUEST_UNSAVED_FILENAME_KEY, "guest.csv");

    clearAccountLocalData();

    expect(sessionStorage.getItem(ANALYSIS_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem(ANALYSIS_OWNER_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem("optionstaxhub-packet-resume-checkout")).toBeNull();
    expect(sessionStorage.getItem("optionstaxhub-packet-paid:run-1")).toBeNull();
    expect(sessionStorage.getItem("custom-packet-resume")).toBeNull();
    expect(localStorage.getItem("optionstaxhub-packet-resume")).toBeNull();
    expect(localStorage.getItem("optionstaxhub-packet-checkout-started")).toBeNull();
    expect(localStorage.getItem("appWasInstalled")).toBe("true");
    expect(sessionStorage.getItem("oth-load-sample")).toBe("1");
    expect(sessionStorage.getItem(GUEST_UNSAVED_KEY)).toBeNull();
    expect(sessionStorage.getItem(GUEST_UNSAVED_FILENAME_KEY)).toBeNull();
    expect(localStorage.getItem(GUEST_UNSAVED_KEY)).toBeNull();
    expect(localStorage.getItem(GUEST_UNSAVED_FILENAME_KEY)).toBeNull();
  });

  it("still clears the other unsaved-guest key when one removal throws", () => {
    sessionStorage.setItem(GUEST_UNSAVED_KEY, "1");
    sessionStorage.setItem(GUEST_UNSAVED_FILENAME_KEY, "guest.csv");
    localStorage.setItem(GUEST_UNSAVED_KEY, "1");
    localStorage.setItem(GUEST_UNSAVED_FILENAME_KEY, "guest.csv");
    const storage = sessionStorage as Storage;
    const originalRemove = storage.removeItem.bind(storage);
    storage.removeItem = (key: string) => {
      if (key === GUEST_UNSAVED_KEY) {
        throw new DOMException("The operation is insecure.", "SecurityError");
      }
      originalRemove(key);
    };
    try {
      clearAccountLocalData();
      expect(sessionStorage.getItem(GUEST_UNSAVED_FILENAME_KEY)).toBeNull();
      expect(localStorage.getItem(GUEST_UNSAVED_KEY)).toBeNull();
      expect(localStorage.getItem(GUEST_UNSAVED_FILENAME_KEY)).toBeNull();
    } finally {
      storage.removeItem = originalRemove;
    }
  });

  it("still removes known keys when storage cannot be enumerated", () => {
    sessionStorage.setItem(ANALYSIS_STORAGE_KEY, "{\"analysis_id\":\"run-1\"}");
    const storage = sessionStorage as Storage & { key?: Storage["key"] };
    const originalKey = storage.key.bind(storage);
    storage.key = () => {
      throw new Error("blocked");
    };
    try {
      clearAccountLocalData();
      expect(sessionStorage.getItem(ANALYSIS_STORAGE_KEY)).toBeNull();
    } finally {
      storage.key = originalKey;
    }
  });

  it("clearStoredAnalysis removes only the run and its owner", () => {
    sessionStorage.setItem(ANALYSIS_STORAGE_KEY, "{\"analysis_id\":\"run-1\"}");
    sessionStorage.setItem(ANALYSIS_OWNER_STORAGE_KEY, "user-a");
    sessionStorage.setItem("optionstaxhub-packet-resume-checkout", "1");
    clearStoredAnalysis();
    expect(sessionStorage.getItem(ANALYSIS_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem(ANALYSIS_OWNER_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem("optionstaxhub-packet-resume-checkout")).toBe("1");
  });
});
