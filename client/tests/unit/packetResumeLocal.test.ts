import {
  clearPacketResumeLocal,
  consumePacketResumeLocalHandoff,
  PACKET_RESUME_REUPLOAD_COPY,
  rememberPacketResumeLocal,
  resetPacketResumeLocalForTests,
} from "../../lib/packetResumeLocal";
import {
  PACKET_RESUME_CHECKOUT_KEY,
  PACKET_RESUME_LOCAL_KEY,
  PACKET_RESUME_LOCAL_TTL_MS,
  PACKET_RESUME_LOCAL_VERSION,
  PACKET_RESUME_SAMPLE_KEY,
} from "../../lib/safeNextPath";

const NOW = 1_700_000_000_000;

function writeRaw(value: unknown): void {
  localStorage.setItem(
    PACKET_RESUME_LOCAL_KEY,
    typeof value === "string" ? value : JSON.stringify(value),
  );
}

describe("packetResumeLocal", () => {
  beforeEach(() => {
    resetPacketResumeLocalForTests();
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
  });

  it("writes only v, sample, and exp", () => {
    rememberPacketResumeLocal(true, NOW);
    const raw = localStorage.getItem(PACKET_RESUME_LOCAL_KEY);
    expect(raw).toBe(
      JSON.stringify({
        v: PACKET_RESUME_LOCAL_VERSION,
        sample: true,
        exp: NOW + PACKET_RESUME_LOCAL_TTL_MS,
      }),
    );
    const parsed = JSON.parse(raw as string) as Record<string, unknown>;
    expect(Object.keys(parsed).sort()).toEqual(["exp", "sample", "v"]);
    expect(raw).not.toMatch(/analysis|tax|position|symbol|csv/i);

    rememberPacketResumeLocal(false, NOW);
    expect(JSON.parse(localStorage.getItem(PACKET_RESUME_LOCAL_KEY) as string)).toEqual({
      v: 1,
      sample: false,
      exp: NOW + PACKET_RESUME_LOCAL_TTL_MS,
    });
  });

  it("restores a valid sample handoff into sessionStorage and deletes the key", () => {
    writeRaw({ v: 1, sample: true, exp: NOW + 1000, analysis_id: "nope" });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBe("1");
    expect(sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY)).toBe("1");
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
  });

  it("keeps an uploaded handoff when the analysis is still stored", () => {
    sessionStorage.setItem("optionstaxhub-analysis", "{\"analysis_id\":\"a\"}");
    writeRaw({ v: 1, sample: false, exp: NOW + 1000 });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBe("1");
    expect(sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY)).toBeNull();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
    expect(PACKET_RESUME_REUPLOAD_COPY).toBe(
      "Re-upload your CSV to continue to checkout.",
    );
  });

  it("asks for a re-upload when an uploaded handoff has no stored analysis", () => {
    writeRaw({ v: 1, sample: false, exp: NOW + 1000 });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(true);
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBeNull();
    expect(sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY)).toBeNull();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
    expect(consumePacketResumeLocalHandoff(NOW + 10)).toBe(true);
  });

  it("ignores an expired key and deletes it", () => {
    writeRaw({ v: 1, sample: true, exp: NOW });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBeNull();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
  });

  it.each([
    ["bad json", "{"],
    ["wrong version", { v: 2, sample: true, exp: NOW + 1000 }],
    ["sample string", { v: 1, sample: "yes", exp: NOW + 1000 }],
    ["missing sample", { v: 1, exp: NOW + 1000 }],
    ["exp string", { v: 1, sample: true, exp: String(NOW + 1000) }],
    ["exp infinite", "{\"v\":1,\"sample\":true,\"exp\":1e309}"],
    ["exp past", { v: 1, sample: true, exp: NOW - 1 }],
    ["array", [{ v: 1, sample: true, exp: NOW + 1000 }]],
    ["null", null],
    ["empty", ""],
  ])("deletes an invalid handoff (%s)", (_label, payload) => {
    writeRaw(payload);
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBeNull();
    expect(sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY)).toBeNull();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
  });

  it("leaves a valid key alone when the page is not resuming checkout", () => {
    window.history.replaceState(null, "", "/dashboard");
    writeRaw({ v: 1, sample: true, exp: NOW + 1000 });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).not.toBeNull();
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBeNull();
  });

  it("does not consume localStorage when the session marker is already set", () => {
    sessionStorage.setItem(PACKET_RESUME_CHECKOUT_KEY, "1");
    writeRaw({ v: 1, sample: true, exp: NOW + 1000 });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).not.toBeNull();
    expect(sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY)).toBeNull();
  });

  it("does nothing when the key is missing", () => {
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBeNull();
  });

  it("keeps the key when storage reads or writes throw", () => {
    writeRaw({ v: 1, sample: true, exp: NOW + 1000 });
    const getItem = jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    getItem.mockRestore();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).not.toBeNull();

    const setItem = jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => rememberPacketResumeLocal(true, NOW)).not.toThrow();
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    setItem.mockRestore();
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBeNull();

    const removeItem = jest
      .spyOn(Storage.prototype, "removeItem")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    expect(() => clearPacketResumeLocal()).not.toThrow();
    sessionStorage.clear();
    writeRaw({ v: 1, sample: false, exp: NOW + 1000 });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(true);
    removeItem.mockRestore();
  });

  it("keeps the key when localStorage cannot be read", () => {
    writeRaw({ v: 1, sample: true, exp: NOW + 1000 });
    const original = Storage.prototype.getItem;
    const getItem = jest
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(function (key) {
        if (key === PACKET_RESUME_LOCAL_KEY) {
          throw new Error("blocked");
        }
        return original.call(this, key as string);
      });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    getItem.mockRestore();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).not.toBeNull();
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBeNull();
  });

  it("leaves the restored marker when the stored-analysis read throws", () => {
    writeRaw({ v: 1, sample: false, exp: NOW + 1000 });
    const original = Storage.prototype.getItem;
    const getItem = jest
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(function (key) {
        if (key === "optionstaxhub-analysis") {
          throw new Error("blocked");
        }
        return original.call(this, key as string);
      });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
    expect(sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY)).toBe("1");
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
    getItem.mockRestore();
  });

  it("stops asking for a re-upload after the test reset", () => {
    writeRaw({ v: 1, sample: false, exp: NOW + 1000 });
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(true);
    resetPacketResumeLocalForTests();
    window.history.replaceState(null, "", "/dashboard");
    expect(consumePacketResumeLocalHandoff(NOW)).toBe(false);
  });
});
