import {
  beginGuestPersist,
  endGuestPersist,
  resetGuestPersistInFlight,
} from "../../lib/guest-persist-lock";

describe("guest persist in-memory lock", () => {
  afterEach(() => {
    resetGuestPersistInFlight();
  });

  it("allows the first persist and rejects a duplicate in the same JS context", () => {
    expect(beginGuestPersist("run-1")).toBe(true);
    expect(beginGuestPersist("run-1")).toBe(false);
    expect(beginGuestPersist("run-2")).toBe(true);
  });

  it("allows a retry after the in-flight persist finishes", () => {
    expect(beginGuestPersist("run-1")).toBe(true);
    endGuestPersist("run-1");
    expect(beginGuestPersist("run-1")).toBe(true);
  });
});
