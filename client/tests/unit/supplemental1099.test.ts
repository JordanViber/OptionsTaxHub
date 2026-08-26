import {
  combinedWashSaleDisallowed,
  dataQualityWarnings,
  formatUsd,
  isSupplemental1099Warning,
  supplemental1099Warnings,
} from "../../lib/supplemental1099";

describe("supplemental1099 helpers", () => {
  it("formats fixture totals and combined wash-sale disallowed", () => {
    expect(formatUsd(281823.83)).toBe("$281,823.83");
    expect(
      combinedWashSaleDisallowed({
        short_term_wash_sale_disallowed: 17409.64,
        long_term_wash_sale_disallowed: 33.16,
      }),
    ).toBeCloseTo(17442.8);
  });

  it("surfaces 1099 warnings separately from other data quality notes", () => {
    const warnings = [
      "Corporate action activity may have changed the reported share count for ASST.",
      "Supplemental 1099 must be a PDF file (received unsupported content type).",
    ];

    expect(isSupplemental1099Warning(warnings[1])).toBe(true);
    expect(supplemental1099Warnings(warnings)).toEqual([warnings[1]]);
    expect(dataQualityWarnings(warnings)).toEqual([warnings[0]]);
  });
});
