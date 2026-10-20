import {
  csvPriceLabel,
  formatLiveAsOf,
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

  it("formats a live ISO timestamp in local time and capitalizes the provider", () => {
    const iso = "2026-10-19T09:06:17+00:00";
    const live: PriceBasis = {
      quotes: [
        {
          symbol: "AAPL",
          price: 10,
          source: "live",
          provider: "yahoo",
          as_of: iso,
        },
      ],
      live: true,
      provider: "yahoo",
      as_of: iso,
      label: "",
    };
    const copy = priceAsOfCopy(live);
    const when = formatLiveAsOf(iso);
    expect(when).toBeTruthy();
    expect(copy).toBe(`Live Yahoo prices as of ${when}`);
    expect(copy).not.toContain("T");
    expect(copy).not.toContain("+00:00");
    expect(copy).not.toContain("yahoo");
    expect(copy).toContain("Yahoo");
  });

  it("omits an unknown provider from the live sentence", () => {
    const iso = "2026-10-19T09:06:17+00:00";
    const live: PriceBasis = {
      quotes: [],
      live: true,
      provider: "nasdaq",
      as_of: iso,
      label: "",
    };
    expect(priceAsOfCopy(live)).toBe(`Live prices as of ${formatLiveAsOf(iso)}`);
    expect(priceAsOfCopy(live)).not.toContain("nasdaq");
  });

  it("falls back to Live prices when the timestamp is missing or unparseable", () => {
    const live: PriceBasis = {
      quotes: [],
      live: true,
      provider: "yahoo",
      as_of: "not-a-timestamp",
      label: "not-a-timestamp",
    };
    expect(priceAsOfCopy(live)).toBe("Live prices");
    expect(priceAsOfCopy({ ...live, as_of: "" })).toBe("Live prices");
    expect(priceAsOfCopy({ ...live, as_of: null })).toBe("Live prices");
    expect(priceAsOfCopy(live)).not.toContain("not-a-timestamp");
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