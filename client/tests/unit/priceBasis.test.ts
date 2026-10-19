import {
  csvPriceLabel,
  priceAsOfCopy,
  pricedNumbersVisible,
} from "../../lib/priceBasis";
import type { PriceBasis } from "../../lib/types";

const csvBasis: PriceBasis = {
  quotes: [
    {
      symbol: "AAPL",
      price: 10,
      source: "csv",
      provider: null,
      as_of: "2026-08-12",
    },
  ],
  live: false,
  csv_as_of: "2026-08-12",
  label: "Prices as of your CSV (2026-08-12)",
};

describe("priceBasis", () => {
  it("formats the CSV label", () => {
    expect(csvPriceLabel("2026-08-12")).toBe(
      "Prices as of your CSV (2026-08-12)",
    );
  });

  it("uses the CSV sentence whenever a quote is not live", () => {
    expect(priceAsOfCopy(csvBasis)).toBe("Prices as of your CSV (2026-08-12)");
    expect(pricedNumbersVisible(csvBasis)).toBe(true);
  });

  it("names the provider only when every quote is live", () => {
    const live: PriceBasis = {
      quotes: [
        {
          symbol: "AAPL",
          price: 10,
          source: "live",
          provider: "yahoo",
          as_of: "2026-10-19T01:00:00+00:00",
        },
      ],
      live: true,
      provider: "yahoo",
      as_of: "2026-10-19T01:00:00+00:00",
      label: "",
    };
    expect(priceAsOfCopy(live)).toBe(
      "Live yahoo prices as of 2026-10-19T01:00:00+00:00",
    );
  });

  it("hides price numbers when a basis has no source or time", () => {
    const incomplete: PriceBasis = {
      quotes: [],
      live: false,
      label: "",
    };
    expect(priceAsOfCopy(incomplete)).toBeNull();
    expect(pricedNumbersVisible(incomplete)).toBe(false);
    expect(pricedNumbersVisible(undefined)).toBe(true);
  });
});