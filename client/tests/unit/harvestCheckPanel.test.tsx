import { render, screen, fireEvent } from "@testing-library/react";
import HarvestCheckPanel from "../../app/components/HarvestCheckPanel";
import {
  harvestBuyKindLabel,
  harvestCountLine,
  harvestDetailOpen,
} from "../../lib/harvestCheck";
import type { HarvestCheck } from "../../lib/types";

function row(overrides: Partial<HarvestCheck> = {}): HarvestCheck {
  return {
    symbol: "SOFI",
    trigger_count: 3,
    clean: false,
    open_loss: 100,
    disallowed_loss: 100,
    earliest_clean_sell_date: "2026-09-10",
    safe_rebuy_date: "2026-10-11",
    triggers: [
      {
        activity_date: "2026-08-01",
        kind: "recurring_buy",
        quantity: 2,
        share_equivalent: 2,
        disallowed_loss: 20,
        permanent_disallowance: false,
      },
      {
        activity_date: "2026-08-05",
        kind: "dividend_reinvestment",
        quantity: 0.5,
        share_equivalent: 0.5,
        disallowed_loss: 5,
        permanent_disallowance: false,
      },
      {
        activity_date: "2026-08-10",
        kind: "bought_call",
        quantity: 1,
        share_equivalent: 100,
        disallowed_loss: 75,
        permanent_disallowance: false,
      },
    ],
    permanent_disallowance: false,
    line: "$100 of your $100 loss may be disallowed",
    as_of: "2026-08-12",
    ...overrides,
  };
}

describe("HarvestCheckPanel", () => {
  it("shows the disallowed line, dates, and each replacement buy", () => {
    render(<HarvestCheckPanel checks={[row()]} detailOpen />);
    expect(screen.getByTestId("harvest-check-SOFI")).toHaveTextContent(
      "$100 of your $100 loss may be disallowed",
    );
    expect(screen.getByTestId("harvest-check-SOFI")).toHaveTextContent(
      "Earliest clean sell date Sep 10, 2026",
    );
    expect(screen.getByTestId("harvest-check-SOFI")).toHaveTextContent(
      "Safe re-buy date Oct 11, 2026",
    );
    expect(
      screen.getByText(/A wash-sale rule may apply, ask a tax pro/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("harvest-check-open-SOFI"));
    const buys = screen.getByTestId("harvest-check-buys-SOFI");
    expect(buys).toHaveTextContent("Aug 1, 2026");
    expect(buys).toHaveTextContent("Recurring buy");
    expect(buys).toHaveTextContent("$20 of the loss");
    expect(buys).toHaveTextContent("Dividend reinvestment");
    expect(buys).toHaveTextContent("$5 of the loss");
    expect(buys).toHaveTextContent("Bought call");
    expect(buys).toHaveTextContent("$75 of the loss");
  });

  it("shows only the count when detail is closed", () => {
    render(
      <HarvestCheckPanel
        checks={[
          row({
            line: "",
            open_loss: null,
            disallowed_loss: null,
            earliest_clean_sell_date: null,
            safe_rebuy_date: null,
            triggers: [],
          }),
        ]}
        detailOpen={false}
      />,
    );
    expect(screen.getByTestId("harvest-check-SOFI")).toHaveTextContent(
      "3 replacement buys",
    );
    expect(screen.queryByText(/\$100/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("harvest-check-open-SOFI")).not.toBeInTheDocument();
  });

  it("keeps the clean sentence and omits TSLA and SPCX rows", () => {
    render(
      <HarvestCheckPanel
        checks={[
          row({
            symbol: "NVDA",
            trigger_count: 0,
            clean: true,
            triggers: [],
            disallowed_loss: 0,
            earliest_clean_sell_date: null,
            safe_rebuy_date: null,
            line: "No replacement buys found in the 30 days either side; may be clean to harvest.",
          }),
          row({
            symbol: "TSLA",
            trigger_count: 0,
            clean: true,
            triggers: [],
            line: "No replacement buys found in the 30 days either side. A wash-sale rule may apply, ask a tax pro.",
            earliest_clean_sell_date: "2026-09-10",
            safe_rebuy_date: "2026-10-11",
          }),
          row({
            symbol: "SPCX",
            trigger_count: 0,
            clean: true,
            triggers: [],
            line: "No replacement buys found in the 30 days either side. A wash-sale rule may apply, ask a tax pro.",
          }),
        ]}
        detailOpen
      />,
    );
    expect(screen.getByTestId("harvest-check-NVDA")).toHaveTextContent(
      "may be clean to harvest",
    );
    expect(screen.queryByTestId("harvest-check-TSLA")).not.toBeInTheDocument();
    expect(screen.queryByTestId("harvest-check-SPCX")).not.toBeInTheDocument();
    expect(screen.queryByText("TSLA")).not.toBeInTheDocument();
    expect(screen.queryByText("SPCX")).not.toBeInTheDocument();
  });

  it("marks an IRA replacement as a permanent disallowance", () => {
    render(
      <HarvestCheckPanel
        checks={[
          row({
            triggers: [
              {
                activity_date: "2026-08-12",
                kind: "regular_buy",
                quantity: 2,
                share_equivalent: 2,
                disallowed_loss: 20,
                account_name: "IRA (Roth)",
                permanent_disallowance: true,
              },
            ],
            trigger_count: 1,
          }),
        ]}
        detailOpen
      />,
    );
    fireEvent.click(screen.getByTestId("harvest-check-open-SOFI"));
    expect(screen.getByText("Permanent disallowance")).toBeInTheDocument();
    expect(screen.getByTestId("harvest-check-buys-SOFI")).toHaveTextContent(
      "IRA (Roth)",
    );
  });

  it("renders nothing when there is no open loss", () => {
    const { container } = render(
      <HarvestCheckPanel checks={[]} detailOpen />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("harvest check helpers", () => {
  it("labels every buy kind", () => {
    expect(harvestBuyKindLabel("regular_buy")).toBe("Regular buy");
    expect(harvestBuyKindLabel("recurring_buy")).toBe("Recurring buy");
    expect(harvestBuyKindLabel("fractional_buy")).toBe("Fractional buy");
    expect(harvestBuyKindLabel("dividend_reinvestment")).toBe(
      "Dividend reinvestment",
    );
    expect(harvestBuyKindLabel("bought_call")).toBe("Bought call");
    expect(harvestBuyKindLabel("short_put")).toBe("Short put");
  });

  it("pluralizes the free count", () => {
    expect(harvestCountLine(0)).toBe("0 replacement buys");
    expect(harvestCountLine(1)).toBe("1 replacement buy");
    expect(harvestCountLine(3)).toBe("3 replacement buys");
  });

  it("opens detail for the sample and a season unlock only", () => {
    expect(harvestDetailOpen({ sample_run: true })).toBe(true);
    expect(harvestDetailOpen({})).toBe(false);
    expect(harvestDetailOpen({ season_detail: true })).toBe(false);
  });
});
