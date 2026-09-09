import {
  getDeskFiles,
  getDeskCsv,
  getDeskForm1099,
  setDeskCsv,
  setDeskForm1099,
  resetDeskFiles,
  subscribeDeskFiles,
} from "../../lib/desk-files";

describe("desk-files", () => {
  beforeEach(() => {
    resetDeskFiles();
  });

  it("starts empty", () => {
    expect(getDeskFiles()).toEqual({ csv: null, form1099: null });
  });

  it("keeps Files after reset is not called", () => {
    const csv = new File(["a"], "book.csv", { type: "text/csv" });
    const pdf = new File(["b"], "1099.pdf", { type: "application/pdf" });
    setDeskCsv(csv);
    setDeskForm1099(pdf);
    expect(getDeskFiles().csv).toBe(csv);
    expect(getDeskFiles().form1099).toBe(pdf);
  });

  it("notifies subscribers", () => {
    const listener = jest.fn();
    const unsub = subscribeDeskFiles(listener);
    setDeskCsv(new File(["a"], "book.csv", { type: "text/csv" }));
    expect(listener).toHaveBeenCalledTimes(1);
    unsub();
    setDeskForm1099(null);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("returns a stable snapshot object until a write", () => {
    const first = getDeskFiles();
    const second = getDeskFiles();
    expect(first).toBe(second);
    setDeskCsv(new File(["a"], "book.csv", { type: "text/csv" }));
    expect(getDeskFiles()).not.toBe(first);
    expect(getDeskCsv()?.name).toBe("book.csv");
    expect(getDeskForm1099()).toBeNull();
  });
});
