import { act, renderHook } from "@testing-library/react";
import { useDeskUploadFiles } from "../../app/dashboard/useDeskUploadFiles";
import { resetDeskFiles, setDeskCsv, setDeskForm1099 } from "../../lib/desk-files";

describe("useDeskUploadFiles", () => {
  beforeEach(() => {
    resetDeskFiles();
  });

  it("rehydrates Files after an unmount / remount", () => {
    const csv = new File(["a"], "book.csv", { type: "text/csv" });
    const pdf = new File(["b"], "1099.pdf", { type: "application/pdf" });

    const first = renderHook(() => useDeskUploadFiles());
    act(() => {
      first.result.current.setLastUploadedCsv(csv);
      first.result.current.setSupplemental1099File(pdf);
    });
    first.unmount();

    const second = renderHook(() => useDeskUploadFiles());
    expect(second.result.current.lastUploadedCsv).toBe(csv);
    expect(second.result.current.supplemental1099File).toBe(pdf);
    second.unmount();
  });

  it("clears Files on resetDeskFiles so a later guest cannot reuse them", () => {
    setDeskCsv(new File(["a"], "book.csv", { type: "text/csv" }));
    setDeskForm1099(new File(["b"], "1099.pdf", { type: "application/pdf" }));
    const { result } = renderHook(() => useDeskUploadFiles());
    act(() => {
      result.current.resetDeskFiles();
    });
    expect(result.current.lastUploadedCsv).toBeNull();
    expect(result.current.supplemental1099File).toBeNull();
  });
});
