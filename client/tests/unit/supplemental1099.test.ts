import {
  classifiedExportWash,
  exportLongTermNet,
  exportShortTermNet,
  isSameYear1099Compare,
  isUnknown1099Year,
  washFlagIsLongTerm,
} from "../../lib/supplemental1099";
import type { RealizedSummary } from "../../lib/types";

const realized: RealizedSummary = {
  tax_year: 2024,
  st_gains: 0,
  st_losses: -300,
  lt_gains: 0,
  lt_losses: 0,
  net_st: -300,
  net_lt: 0,
  total_net: -300,
  transactions_count: 1,
};

const stFlag = {
  purchase_date: "2024-06-01",
  sale_date: "2024-07-15",
  repurchase_date: "2024-07-24",
  disallowed_loss: 300,
};

const ltFlag = {
  purchase_date: "2023-01-01",
  sale_date: "2024-07-15",
  repurchase_date: "2024-07-24",
  disallowed_loss: 300,
};

describe("supplemental1099 helpers", () => {
  it("folds classified CSV wash into export ST net to match the 1099 definition", () => {
    expect(classifiedExportWash(300)).toEqual({
      shortTerm: 300,
      longTerm: 0,
    });
    expect(classifiedExportWash([stFlag])).toEqual({
      shortTerm: 300,
      longTerm: 0,
    });
    expect(exportShortTermNet(realized, 300)).toBe(0);
    expect(exportShortTermNet(realized, [stFlag])).toBe(0);
    expect(exportLongTermNet(realized, 300)).toBe(0);
    expect(exportShortTermNet(realized, 0)).toBe(-300);
  });

  it("classifies a long-term disallowed sale as LT, not dumped into ST losses", () => {
    const mixed: RealizedSummary = {
      ...realized,
      st_losses: -1000,
      lt_losses: -300,
      net_st: -1000,
      net_lt: -300,
      total_net: -1300,
    };
    expect(washFlagIsLongTerm(ltFlag)).toBe(true);
    expect(classifiedExportWash([ltFlag])).toEqual({
      shortTerm: 0,
      longTerm: 300,
    });
    expect(exportShortTermNet(mixed, [ltFlag])).toBe(-1000);
    expect(exportLongTermNet(mixed, [ltFlag])).toBe(0);
  });

  it("keeps $300 ST loss + $300 ST disallowed at $0 vs $0", () => {
    expect(exportShortTermNet(realized, [stFlag])).toBe(0);
    expect(exportLongTermNet(realized, [stFlag])).toBe(0);
  });

  it("does not synthesize +$300 ST net when realized_summary is missing or null", () => {
    expect(exportShortTermNet(null, 300)).toBe(0);
    expect(exportShortTermNet(undefined, 300)).toBe(0);
    expect(exportShortTermNet(null, [stFlag])).toBe(0);
    expect(exportLongTermNet(null, [ltFlag])).toBe(0);
    expect(exportShortTermNet({} as RealizedSummary, [stFlag])).toBe(0);
  });

  it("uses sale_date, falling back to repurchase, against purchase_date for term", () => {
    expect(
      washFlagIsLongTerm({
        purchase_date: "2023-01-01",
        sale_date: "2024-01-01",
        disallowed_loss: 1,
      }),
    ).toBe(false);
    expect(
      washFlagIsLongTerm({
        purchase_date: "2023-01-01",
        sale_date: "2024-01-02",
        disallowed_loss: 1,
      }),
    ).toBe(true);
    expect(
      washFlagIsLongTerm({
        purchase_date: "2023-01-01",
        repurchase_date: "2024-07-24",
        disallowed_loss: 1,
      }),
    ).toBe(true);
    expect(
      washFlagIsLongTerm({
        sale_date: "2024-07-15",
        repurchase_date: "2024-07-24",
        disallowed_loss: 300,
      }),
    ).toBe(false);
  });

  it("treats a missing 1099 year as unknown, never as a same-year compare", () => {
    expect(isUnknown1099Year(null)).toBe(true);
    expect(isUnknown1099Year(undefined)).toBe(true);
    expect(isUnknown1099Year(2024)).toBe(false);
    expect(isSameYear1099Compare(null, 2024)).toBe(false);
    expect(isSameYear1099Compare(undefined, 2024)).toBe(false);
  });
});
