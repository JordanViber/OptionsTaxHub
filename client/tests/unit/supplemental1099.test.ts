import {
  classifiedExportWash,
  exportLongTermNet,
  exportShortTermNet,
  isSameYear1099Compare,
  isUnknown1099Year,
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

describe("supplemental1099 helpers", () => {
  it("folds classified CSV wash into export ST net to match the 1099 definition", () => {
    expect(classifiedExportWash(realized, 300)).toEqual({
      shortTerm: 300,
      longTerm: 0,
    });
    expect(exportShortTermNet(realized, 300)).toBe(0);
    expect(exportLongTermNet(realized, 300)).toBe(0);
    expect(exportShortTermNet(realized, 0)).toBe(-300);
    expect(exportShortTermNet(null, 300)).toBe(300);
  });

  it("folds classified wash into LT net when only LT losses exist", () => {
    const ltOnly: RealizedSummary = {
      ...realized,
      st_losses: 0,
      net_st: 0,
      lt_losses: -300,
      net_lt: -300,
    };
    expect(classifiedExportWash(ltOnly, 300)).toEqual({
      shortTerm: 0,
      longTerm: 300,
    });
    expect(exportShortTermNet(ltOnly, 300)).toBe(0);
    expect(exportLongTermNet(ltOnly, 300)).toBe(0);
  });

  it("splits classified wash across ST then LT loss buckets", () => {
    const mixed: RealizedSummary = {
      ...realized,
      st_losses: -200,
      lt_losses: -100,
      net_st: -200,
      net_lt: -100,
      total_net: -300,
    };
    expect(classifiedExportWash(mixed, 300)).toEqual({
      shortTerm: 200,
      longTerm: 100,
    });
    expect(exportShortTermNet(mixed, 300)).toBe(0);
    expect(exportLongTermNet(mixed, 300)).toBe(0);
  });

  it("treats a missing 1099 year as unknown, never as a same-year compare", () => {
    expect(isUnknown1099Year(null)).toBe(true);
    expect(isUnknown1099Year(undefined)).toBe(true);
    expect(isUnknown1099Year(2024)).toBe(false);
    expect(isSameYear1099Compare(null, 2024)).toBe(false);
    expect(isSameYear1099Compare(undefined, 2024)).toBe(false);
  });
});
