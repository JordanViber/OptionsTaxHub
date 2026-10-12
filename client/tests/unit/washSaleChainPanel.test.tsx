import { render, screen, fireEvent } from "@testing-library/react";
import WashSaleChainPanel, {
  WASH_SALE_CHAIN_DISCLAIMER,
  washChainsVisible,
  washChainStatusLabel,
} from "../../app/components/WashSaleChainPanel";
import type { WashSaleChain, WashSaleHop } from "../../lib/types";

const amdPlain =
  "You sold 10 AMD at a $300 loss on Jul 15 and bought 10 back on Jul 24, so $300 moved into the new lot's basis. This matches your 1099 wash adjustment of $300.";

function hop(overrides: Partial<WashSaleHop> = {}): WashSaleHop {
  return {
    hop_id: "AMD:2026-07-15:10:e0",
    hop_index: 0,
    symbol: "AMD",
    asset_type: "stock",
    sale_date: "2026-07-15",
    sale_quantity: 10,
    proceeds: 1200,
    cost_basis: 1500,
    loss: 300,
    replacement_buys: [
      {
        activity_date: "2026-07-24",
        quantity: 10,
        price: 125,
        cash_cost: 1250,
        allocated_quantity: 10,
        allocated_disallowed: 300,
        explained_basis: 1550,
      },
    ],
    disallowed_loss: 300,
    engine_disallowed_loss: 300,
    form_1099_wash: 300,
    engine_adjusted_cost_basis: 1550,
    open_lot_total_cost_basis: 1550,
    open_lot_wash_sale_disallowed: 300,
    original_purchase_date: "2026-06-01",
    original_holding_days: 44,
    carryover_days: 44,
    tacked_acquisition_date: "2026-06-01",
    carried_in_disallowed: 0,
    engine_sale_loss: 300,
    explained_loss_with_carry: 300,
    plain_english: amdPlain,
    reconcile_status: "matches",
    difference: null,
    ...overrides,
  };
}

function chain(overrides: Partial<WashSaleChain> = {}): WashSaleChain {
  const head = hop();
  return {
    chain_id: head.hop_id,
    symbol: "AMD",
    reconcile_status: "matches",
    difference: null,
    plain_english: amdPlain,
    hops: [head],
    ...overrides,
  };
}

function stubMatchMedia(matches: boolean) {
  Object.defineProperty(globalThis, "matchMedia", {
    writable: true,
    value: jest.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    })),
  });
}

describe("washChainsVisible", () => {
  const sample = chain();

  it("shows chains for the guest sample and a paid packet", () => {
    expect(
      washChainsVisible({ sample_run: true, wash_sale_chains: [sample] }, false),
    ).toBe(true);
    expect(
      washChainsVisible({ sample_run: false, wash_sale_chains: [sample] }, true),
    ).toBe(true);
  });

  it("hides chains on an unpaid upload and when history has no chains", () => {
    expect(
      washChainsVisible({ sample_run: false, wash_sale_chains: [sample] }, false),
    ).toBe(false);
    expect(washChainsVisible({ sample_run: true }, false)).toBe(false);
    expect(
      washChainsVisible({ sample_run: true, wash_sale_chains: [] }, true),
    ).toBe(false);
    expect(washChainsVisible({ wash_sale_chains: null }, true)).toBe(false);
  });
});

describe("washChainStatusLabel", () => {
  it("formats each reconcile status", () => {
    expect(washChainStatusLabel("matches", null)).toBe("Matches");
    expect(washChainStatusLabel("differs", 20)).toBe("Differs by $20");
    expect(washChainStatusLabel("differs", 20.5)).toBe("Differs by $20.50");
    expect(washChainStatusLabel("differs", null)).toBe("Differs by $0");
    expect(washChainStatusLabel("not_on_1099", null)).toBe("Not on your 1099");
    expect(washChainStatusLabel("on_1099_only", null)).toBe("On your 1099 only");
    expect(washChainStatusLabel("not_compared", null)).toBe("Not compared");
    expect(washChainStatusLabel("other", null)).toBe("other");
  });
});

describe("WashSaleChainPanel", () => {
  afterEach(() => {
    stubMatchMedia(false);
  });

  it("renders nothing when there are no chains", () => {
    const { container } = render(<WashSaleChainPanel chains={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the AMD headline, Matches, and the disclaimer", () => {
    render(<WashSaleChainPanel chains={[chain()]} />);
    expect(screen.getByTestId("wash-sale-chain-card")).toBeInTheDocument();
    expect(screen.getByText("Wash-sale chains")).toBeInTheDocument();
    expect(screen.getByText(amdPlain)).toBeInTheDocument();
    expect(screen.getByText("Matches")).toBeInTheDocument();
    const disclaimer = screen.getByTestId("wash-sale-chain-disclaimer");
    expect(disclaimer).toHaveTextContent(/not tax advice/i);
    expect(disclaimer).toHaveTextContent("IRAs");
    expect(disclaimer.textContent).not.toMatch(/we detected every wash/i);
    expect(disclaimer.textContent).not.toMatch(/other accounts are included/i);
    expect(disclaimer).toHaveTextContent(WASH_SALE_CHAIN_DISCLAIMER);
  });

  it("opens the AMD chain with basis, carryover, and the 1099 figure", () => {
    render(<WashSaleChainPanel chains={[chain()]} />);
    fireEvent.click(screen.getByTestId("wash-sale-chain-open-AMD:2026-07-15:10:e0"));
    expect(screen.getByTestId("wash-sale-chain-dialog")).toBeInTheDocument();
    expect(screen.getByText("$1,200.00")).toBeInTheDocument();
    expect(screen.getByText("$1,500.00")).toBeInTheDocument();
    expect(screen.getAllByText("$300.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$1,550.00").length).toBeGreaterThan(0);
    expect(screen.getByText(/Jul 24, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/explained basis/i)).toHaveTextContent("10");
    expect(
      screen.getByText(/44 days carried from the sold lot/),
    ).toHaveTextContent("Jun 1, 2026");
    expect(
      screen.getByText("Engine adjusted basis (source of truth)"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/in your tax totals/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText("Per-buy basis split is illustrative."),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Open lot basis now")).toBeInTheDocument();
    expect(screen.getByText("1099 wash adjustment")).toBeInTheDocument();
    expect(screen.getAllByTestId("wash-sale-chain-disclaimer")).toHaveLength(1);
    expect(
      screen.getByTestId("wash-sale-chain-dialog-disclaimer"),
    ).toHaveTextContent(WASH_SALE_CHAIN_DISCLAIMER);
  });

  it("labels Differs, Not on your 1099, and On your 1099 only", () => {
    const differs = chain({
      chain_id: "DIFF",
      symbol: "NVDA",
      reconcile_status: "differs",
      difference: 20,
      plain_english: "Differs example",
      hops: [
        hop({
          hop_id: "DIFF",
          symbol: "NVDA",
          reconcile_status: "differs",
          difference: 20,
          plain_english: "Differs example",
          form_1099_wash: 280,
        }),
      ],
    });
    const absent = chain({
      chain_id: "MISS",
      symbol: "TSLA",
      reconcile_status: "not_on_1099",
      plain_english: "Export only example",
      hops: [
        hop({
          hop_id: "MISS",
          symbol: "TSLA",
          reconcile_status: "not_on_1099",
          plain_english: "Export only example",
          form_1099_wash: null,
          engine_adjusted_cost_basis: null,
          open_lot_total_cost_basis: null,
          tacked_acquisition_date: null,
          carryover_days: null,
          replacement_buys: [],
        }),
      ],
    });
    const brokerOnly = chain({
      chain_id: "ONLY",
      symbol: "QQQ",
      reconcile_status: "on_1099_only",
      plain_english: "1099 only example",
      hops: [
        hop({
          hop_id: "ONLY",
          symbol: "QQQ",
          reconcile_status: "on_1099_only",
          plain_english: "1099 only example",
          engine_disallowed_loss: null,
          engine_adjusted_cost_basis: null,
          disallowed_loss: 50,
        }),
      ],
    });
    render(<WashSaleChainPanel chains={[differs, absent, brokerOnly]} />);
    expect(screen.getAllByText("Differs by $20").length).toBeGreaterThan(0);
    expect(screen.getByText("Not on your 1099")).toBeInTheDocument();
    expect(screen.getByText("On your 1099 only")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("wash-sale-chain-open-MISS"));
    expect(
      screen.getByText("Holding-period carryover is not in this export."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No replacement buy in this Robinhood export."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Engine adjusted basis (source of truth)"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/in your tax totals/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Close wash-sale chain"));
    expect(
      screen.queryByText("Holding-period carryover is not in this export."),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("No replacement buy in this Robinhood export."),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("wash-sale-chain-open-MISS"));
    expect(
      screen.getByText("Holding-period carryover is not in this export."),
    ).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("dialog"), {
      key: "Escape",
      code: "Escape",
    });
    expect(
      screen.queryByText("Holding-period carryover is not in this export."),
    ).not.toBeInTheDocument();
  });

  it("lists both buys on a partial replacement", () => {
    const partial = chain({
      chain_id: "AAPL:2026-06-01:100",
      symbol: "AAPL",
      reconcile_status: "not_on_1099",
      plain_english: "Partial headline",
      hops: [
        hop({
          hop_id: "AAPL:2026-06-01:100",
          symbol: "AAPL",
          sale_date: "2026-06-01",
          sale_quantity: 100,
          reconcile_status: "not_on_1099",
          plain_english: "Partial headline",
          replacement_buys: [
            {
              activity_date: "2026-06-06",
              quantity: 30,
              price: 160,
              cash_cost: 4800,
              allocated_quantity: 30,
              allocated_disallowed: 1500,
              explained_basis: 6300,
            },
            {
              activity_date: "2026-06-11",
              quantity: 20,
              price: 158,
              cash_cost: 3160,
              allocated_quantity: 20,
              allocated_disallowed: 1000,
              explained_basis: 4160,
            },
          ],
        }),
      ],
    });
    render(<WashSaleChainPanel chains={[partial]} />);
    fireEvent.click(
      screen.getByTestId("wash-sale-chain-open-AAPL:2026-06-01:100"),
    );
    expect(screen.getByText(/Jun 6, 2026 · 30/)).toBeInTheDocument();
    expect(screen.getByText(/Jun 11, 2026 · 20/)).toBeInTheDocument();
  });

  it("shows hop 2 engine loss beside the carried amount", () => {
    const first = hop({ plain_english: "Hop one headline" });
    const second = hop({
      hop_id: "AAPL:2026-06-20:10",
      hop_index: 1,
      symbol: "AAPL",
      sale_date: "2026-06-20",
      proceeds: 700,
      cost_basis: 900,
      loss: 200,
      disallowed_loss: 200,
      engine_sale_loss: 200,
      explained_loss_with_carry: 400,
      carried_in_disallowed: 200,
      plain_english:
        "Hop 2: You bought 10 AAPL on Jun 10 and sold 10 at a $200 loss on Jun 20. The prior hop carried $200 into this lot. The engine still reports a $200 loss on this sale. That engine figure is the source of truth.",
    });
    render(
      <WashSaleChainPanel
        chains={[
          chain({
            chain_id: first.hop_id,
            plain_english: first.plain_english,
            hops: [first, second],
          }),
        ]}
      />,
    );
    expect(screen.getByText("Hop one headline")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`wash-sale-chain-open-${first.hop_id}`));
    expect(
      screen.getByText(/That engine figure is the source of truth/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/in your tax totals/i)).not.toBeInTheDocument();
    expect(screen.getByText("Carried in")).toBeInTheDocument();
    expect(screen.getByText("Engine loss on this sale")).toBeInTheDocument();
    expect(screen.getByText("Explained loss with carry")).toBeInTheDocument();
    expect(screen.getByText("$400.00")).toBeInTheDocument();
    expect(screen.getByText("Hop 2")).toBeInTheDocument();
  });

  it("uses a full-screen dialog at phone width and still shows an empty hop list", () => {
    stubMatchMedia(true);
    render(
      <WashSaleChainPanel
        chains={[
          chain({
            hops: [],
            plain_english: "Headline only",
          }),
        ]}
      />,
    );
    fireEvent.click(screen.getByTestId("wash-sale-chain-open-AMD:2026-07-15:10:e0"));
    expect(document.querySelector(".MuiDialog-paperFullScreen")).toBeTruthy();
    expect(screen.getAllByText("Headline only").length).toBeGreaterThan(0);
  });

  it("leaves a non-ISO sale date as typed", () => {
    render(
      <WashSaleChainPanel
        chains={[
          chain({
            hops: [
              hop({
                sale_date: "not-a-date",
                tacked_acquisition_date: "also-bad",
                carryover_days: 3,
                replacement_buys: [
                  {
                    activity_date: "nope",
                    quantity: 1.5,
                    price: 1,
                    cash_cost: 1.5,
                    allocated_quantity: 1.5,
                    allocated_disallowed: 0,
                    explained_basis: 1.5,
                  },
                  {
                    activity_date: "2026-06-02",
                    quantity: 4,
                    price: 1,
                    cash_cost: 0,
                    allocated_quantity: 0,
                    allocated_disallowed: 0,
                    explained_basis: 0,
                  },
                ],
              }),
            ],
          }),
        ]}
      />,
    );
    fireEvent.click(screen.getByTestId("wash-sale-chain-open-AMD:2026-07-15:10:e0"));
    expect(screen.getByText("not-a-date")).toBeInTheDocument();
    expect(
      screen.getByText("Holding-period carryover is not in this export."),
    ).toBeInTheDocument();
    expect(screen.getByText(/nope · 1.5/)).toBeInTheDocument();
    expect(screen.queryByText(/Jun 2, 2026 · 4/)).not.toBeInTheDocument();
  });

  it("marks a per-buy split as illustrative and keeps duplicate buy keys", () => {
    render(
      <WashSaleChainPanel
        chains={[
          chain({
            reconcile_status: "not_compared",
            hops: [
              hop({
                reconcile_status: "not_compared",
                engine_adjusted_cost_basis: 1550,
                replacement_buys: [
                  {
                    activity_date: "2026-07-24",
                    quantity: 10,
                    price: 125,
                    cash_cost: 1250,
                    allocated_quantity: 6,
                    allocated_disallowed: 180,
                    explained_basis: 930,
                  },
                  {
                    activity_date: "2026-07-24",
                    quantity: 10,
                    price: 130,
                    cash_cost: 1300,
                    allocated_quantity: 4,
                    allocated_disallowed: 120,
                    explained_basis: 640,
                  },
                ],
              }),
            ],
          }),
        ]}
      />,
    );
    expect(screen.getByText("Not compared")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("wash-sale-chain-open-AMD:2026-07-15:10:e0"));
    expect(
      screen.getByText("Per-buy basis split is illustrative."),
    ).toBeInTheDocument();
    expect(screen.getByText(/explained basis \$930\.00/)).toBeInTheDocument();
    expect(screen.getByText(/explained basis \$640\.00/)).toBeInTheDocument();
    expect(screen.queryByText(/in your tax totals/i)).not.toBeInTheDocument();
  });
});
