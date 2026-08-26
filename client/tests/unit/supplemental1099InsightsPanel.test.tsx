import { render, screen } from "@testing-library/react";
import Supplemental1099InsightsPanel from "../../app/components/Supplemental1099InsightsPanel";
import type { Supplemental1099Summary } from "../../lib/types";
import {
  SUPPLEMENTAL_1099_APPLIED_COPY,
  SUPPLEMENTAL_1099_APPLIED_TITLE,
  SUPPLEMENTAL_1099_SETTLEMENT_FAQ,
  SUPPLEMENTAL_1099_WASH_SALE_FAQ,
} from "../../lib/supplemental1099";

const fixtureSummary: Supplemental1099Summary = {
  source_filename: "c15f7458-e9d5-4dfb-a985-351df5a36cde.pdf",
  broker_name: "Robinhood",
  tax_year: 2024,
  short_term_proceeds: 281823.83,
  short_term_cost_basis: 264439.89,
  short_term_wash_sale_disallowed: 17409.64,
  short_term_net_gain: 34793.58,
  long_term_proceeds: 108.56,
  long_term_cost_basis: 141.72,
  long_term_wash_sale_disallowed: 33.16,
  long_term_net_gain: 0,
  referenced_symbols: ["CLSK", "TSLL"],
  matched_symbols: ["CLSK"],
  insights: [
    "The supplemental Robinhood 1099 matches the expected prior tax year (2024).",
  ],
};

describe("Supplemental1099InsightsPanel", () => {
  it("shows applied copy, tax year, ST/LT totals, and combined wash-sale from the fixture", () => {
    render(<Supplemental1099InsightsPanel summary={fixtureSummary} />);

    expect(screen.getByText(SUPPLEMENTAL_1099_APPLIED_TITLE)).toBeInTheDocument();
    expect(screen.getByText(SUPPLEMENTAL_1099_APPLIED_COPY)).toBeInTheDocument();
    expect(
      screen.getByText(/Using Robinhood 1099 PDF for tax year 2024/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Short-term proceeds")).toBeInTheDocument();
    expect(screen.getByText("$281,823.83")).toBeInTheDocument();
    expect(screen.getByText("Long-term proceeds")).toBeInTheDocument();
    expect(screen.getByText("$108.56")).toBeInTheDocument();
    expect(screen.getByText("Wash-sale disallowed")).toBeInTheDocument();
    expect(screen.getByText("$17,442.80")).toBeInTheDocument();
  });

  it("always shows the settlement-date FAQ as totals-only context", () => {
    render(<Supplemental1099InsightsPanel summary={fixtureSummary} />);

    expect(screen.getByText(SUPPLEMENTAL_1099_SETTLEMENT_FAQ)).toBeInTheDocument();
    expect(screen.getByText(/SPX 12\/31/i)).toBeInTheDocument();
    expect(
      screen.getByText(/do not parse settlement-date lots from the PDF/i),
    ).toBeInTheDocument();
  });

  it("shows the wash-sale gray-area FAQ when the 1099 reported disallowed wash-sale", () => {
    render(<Supplemental1099InsightsPanel summary={fixtureSummary} />);

    expect(screen.getByText(SUPPLEMENTAL_1099_WASH_SALE_FAQ)).toBeInTheDocument();
  });

  it("hides the wash-sale gray-area FAQ when disallowed wash-sale is zero", () => {
    render(
      <Supplemental1099InsightsPanel
        summary={{
          ...fixtureSummary,
          short_term_wash_sale_disallowed: 0,
          long_term_wash_sale_disallowed: 0,
        }}
      />,
    );

    expect(
      screen.queryByText(SUPPLEMENTAL_1099_WASH_SALE_FAQ),
    ).not.toBeInTheDocument();
    expect(screen.getByText(SUPPLEMENTAL_1099_SETTLEMENT_FAQ)).toBeInTheDocument();
  });

  it("keeps a year mismatch visible instead of treating a 2023 1099 as 2024", () => {
    render(
      <Supplemental1099InsightsPanel
        summary={{
          ...fixtureSummary,
          tax_year: 2023,
          insights: [
            "The supplemental Robinhood 1099 is for tax year 2023, not the expected prior year (2024).",
          ],
        }}
      />,
    );

    expect(
      screen.getByText(/Using Robinhood 1099 PDF for tax year 2023/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /for tax year 2023, not the expected prior year \(2024\)/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Using Robinhood 1099 PDF for tax year 2024/i),
    ).not.toBeInTheDocument();
  });
});
