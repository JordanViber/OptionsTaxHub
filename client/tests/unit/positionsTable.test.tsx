import { fireEvent, render, screen } from "@testing-library/react";
import PositionsTable from "../../app/components/PositionsTable";
import type { Position, TaxLot, WashSaleFlag } from "@/lib/types";

// Mock MUI DataGrid to avoid license warnings and simplify testing
jest.mock("@mui/x-data-grid", () => ({
  DataGrid: ({
    rows,
    columns,
    getRowClassName,
    onRowClick,
  }: Readonly<Record<string, unknown>>) => {
    const rowsArray = Array.isArray(rows) ? rows : [];
    const columnsArray = Array.isArray(columns) ? columns : [];
    const symbolColumn = columnsArray.find(
      (column: unknown) =>
        typeof column === "object" &&
        column !== null &&
        "field" in (column as Record<string, unknown>) &&
        (column as Record<string, unknown>).field === "symbol",
    ) as
      | {
          renderCell?: (params: {
            row: Position;
            value: string;
          }) => React.ReactNode;
        }
      | undefined;

    return (
      <div data-testid="positions-table">
        {rowsArray.map((row: unknown) => {
          const position = row as Position;
          const rowClass =
            (getRowClassName as (params: Record<string, unknown>) => string)?.({
              row: position,
            }) || "";
          const renderedSymbol = symbolColumn?.renderCell?.({
            row: position,
            value: position.symbol,
          });
          return (
            <div
              key={position.symbol}
              data-testid={`position-row-${position.symbol}`}
              className={rowClass}
              data-pnl={position.unrealized_pnl}
              onClick={() =>
                (
                  onRowClick as
                    | ((params: { row: Position }) => void)
                    | undefined
                )?.({ row: position })
              }
            >
              <div data-testid={`rendered-symbol-${position.symbol}`}>
                {renderedSymbol}
              </div>
              <span data-testid={`symbol-${position.symbol}`}>
                {position.symbol}
              </span>
              <span data-testid={`qty-${position.symbol}`}>
                {position.quantity}
              </span>
              <span data-testid={`cost-${position.symbol}`}>
                {position.avg_cost_basis}
              </span>
              <span data-testid={`price-${position.symbol}`}>
                {position.current_price}
              </span>
              <span data-testid={`value-${position.symbol}`}>
                {position.market_value}
              </span>
              <span data-testid={`pnl-${position.symbol}`}>
                {position.unrealized_pnl}
              </span>
              <span data-testid={`days-${position.symbol}`}>
                {position.holding_period_days}
              </span>
              <span data-testid={`long-term-${position.symbol}`}>
                {position.is_long_term ? "LT" : "ST"}
              </span>
              <span data-testid={`wash-sale-${position.symbol}`}>
                {position.wash_sale_risk ? "Risk" : "None"}
              </span>
              <span data-testid={`type-${position.symbol}`}>
                {position.asset_type}
              </span>
            </div>
          );
        })}
      </div>
    );
  },
}));

describe("PositionsTable", () => {
  describe("Rendering", () => {
    it("renders positions table", () => {
      const positions: Position[] = [];
      render(<PositionsTable positions={positions} />);

      expect(screen.getByTestId("positions-table")).toBeInTheDocument();
    });

    it("renders all positions", () => {
      const positions: Position[] = [
        {
          symbol: "AAPL",
          quantity: 100,
          avg_cost_basis: 150,
          current_price: 175,
          market_value: 17500,
          unrealized_pnl: 2500,
          unrealized_pnl_pct: 16.67,
          holding_period_days: 180,
          is_long_term: true,
          wash_sale_risk: false,
          asset_type: "stock",
          total_cost_basis: 0,
          earliest_purchase_date: "",
          tax_lots: [],
        },
        {
          symbol: "GOOGL",
          quantity: 50,
          avg_cost_basis: 100,
          current_price: 120,
          market_value: 6000,
          unrealized_pnl: 1000,
          unrealized_pnl_pct: 20,
          holding_period_days: 365,
          is_long_term: true,
          wash_sale_risk: false,
          asset_type: "stock",
          total_cost_basis: 0,
          earliest_purchase_date: "",
          tax_lots: [],
        },
      ];

      render(<PositionsTable positions={positions} />);

      expect(screen.getByTestId("symbol-AAPL")).toBeInTheDocument();
      expect(screen.getByTestId("symbol-GOOGL")).toBeInTheDocument();
    });

    it("renders correct position data", () => {
      const position: Position = {
        symbol: "AAPL",
        quantity: 100,
        avg_cost_basis: 150,
        current_price: 175,
        market_value: 17500,
        unrealized_pnl: 2500,
        unrealized_pnl_pct: 16.67,
        holding_period_days: 180,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("qty-AAPL")).toHaveTextContent("100");
      expect(screen.getByTestId("cost-AAPL")).toHaveTextContent("150");
      expect(screen.getByTestId("price-AAPL")).toHaveTextContent("175");
      expect(screen.getByTestId("value-AAPL")).toHaveTextContent("17500");
      expect(screen.getByTestId("pnl-AAPL")).toHaveTextContent("2500");
    });

    it("shows manual-review note for affected positions", () => {
      const position: Position = {
        symbol: "ASST",
        display_label: "ASST",
        manual_review_required: true,
        manual_review_reason:
          "Recent stock split activity affected ASST. Verify reported quantities, adjusted contracts, and cost basis manually before acting.",
        quantity: 3,
        avg_cost_basis: 1.25,
        current_price: 0.85,
        market_value: 2.55,
        unrealized_pnl: -1.2,
        unrealized_pnl_pct: -32,
        holding_period_days: 45,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 3.75,
        earliest_purchase_date: "2026-01-01",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("rendered-symbol-ASST")).toHaveTextContent(
        "Manual review",
      );
      expect(
        screen.getByLabelText(/Recent stock split activity affected ASST/i),
      ).toBeInTheDocument();
    });

    it("renders empty list of positions", () => {
      const positions: Position[] = [];
      const { container } = render(<PositionsTable positions={positions} />);

      expect(screen.getByTestId("positions-table")).toBeInTheDocument();
      expect(
        container.querySelectorAll("[data-testid^='position-row-']"),
      ).toHaveLength(0);
    });
  });

  describe("Row Styling", () => {
    it("applies loss-row class for negative P&L", () => {
      const position: Position = {
        symbol: "TSLA",
        quantity: 50,
        avg_cost_basis: 200,
        current_price: 150, // Loss
        market_value: 7500,
        unrealized_pnl: -2500,
        unrealized_pnl_pct: -25,
        holding_period_days: 90,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      const { container } = render(<PositionsTable positions={[position]} />);
      const row = container.querySelector('[data-testid="position-row-TSLA"]');

      expect(row).toHaveClass("loss-row");
    });

    it("applies gain-row class for positive P&L", () => {
      const position: Position = {
        symbol: "AAPL",
        quantity: 100,
        avg_cost_basis: 150,
        current_price: 175, // Gain
        market_value: 17500,
        unrealized_pnl: 2500,
        unrealized_pnl_pct: 16.67,
        holding_period_days: 180,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      const { container } = render(<PositionsTable positions={[position]} />);
      const row = container.querySelector('[data-testid="position-row-AAPL"]');

      expect(row).toHaveClass("gain-row");
    });

    it("applies no class for zero P&L", () => {
      const position: Position = {
        symbol: "GOOGL",
        quantity: 100,
        avg_cost_basis: 100,
        current_price: 100, // Break even
        market_value: 10000,
        unrealized_pnl: 0,
        unrealized_pnl_pct: 0,
        holding_period_days: 90,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      const { container } = render(<PositionsTable positions={[position]} />);
      const row = container.querySelector('[data-testid="position-row-GOOGL"]');

      expect(row).not.toHaveClass("loss-row");
      expect(row).not.toHaveClass("gain-row");
    });

    it("applies no class for null P&L", () => {
      const position: Position = {
        symbol: "MSFT",
        quantity: 50,
        avg_cost_basis: 300,
        current_price: null,
        market_value: null,
        unrealized_pnl: null,
        unrealized_pnl_pct: null,
        holding_period_days: 45,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      const { container } = render(<PositionsTable positions={[position]} />);
      const row = container.querySelector('[data-testid="position-row-MSFT"]');

      expect(row).not.toHaveClass("loss-row");
      expect(row).not.toHaveClass("gain-row");
    });
  });

  describe("Long-Term vs Short-Term", () => {
    it("displays long-term badge for positions held over 1 year", () => {
      const position: Position = {
        symbol: "AAPL",
        quantity: 100,
        avg_cost_basis: 150,
        current_price: 175,
        market_value: 17500,
        unrealized_pnl: 2500,
        unrealized_pnl_pct: 16.67,
        holding_period_days: 365,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("long-term-AAPL")).toHaveTextContent("LT");
    });

    it("displays short-term badge for positions held under 1 year", () => {
      const position: Position = {
        symbol: "TSLA",
        quantity: 50,
        avg_cost_basis: 200,
        current_price: 220,
        market_value: 11000,
        unrealized_pnl: 1000,
        unrealized_pnl_pct: 10,
        holding_period_days: 90,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("long-term-TSLA")).toHaveTextContent("ST");
    });

    it("displays holding period in days", () => {
      const position: Position = {
        symbol: "MSFT",
        quantity: 75,
        avg_cost_basis: 300,
        current_price: 350,
        market_value: 26250,
        unrealized_pnl: 3750,
        unrealized_pnl_pct: 16.67,
        holding_period_days: 180,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("days-MSFT")).toHaveTextContent("180");
    });
  });

  describe("Wash Sale Risk", () => {
    it("displays wash sale risk warning when present", () => {
      const position: Position = {
        symbol: "TSLA",
        quantity: 50,
        avg_cost_basis: 200,
        current_price: 180,
        market_value: 9000,
        unrealized_pnl: -1000,
        unrealized_pnl_pct: -10,
        holding_period_days: 15,
        is_long_term: false,
        wash_sale_risk: true,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("wash-sale-TSLA")).toHaveTextContent("Risk");
    });

    it("does not display wash sale risk when absent", () => {
      const position: Position = {
        symbol: "AAPL",
        quantity: 100,
        avg_cost_basis: 150,
        current_price: 140,
        market_value: 14000,
        unrealized_pnl: -1000,
        unrealized_pnl_pct: -6.67,
        holding_period_days: 180,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("wash-sale-AAPL")).toHaveTextContent("None");
    });
  });

  describe("Asset Type", () => {
    it("displays stock type for stock positions", () => {
      const position: Position = {
        symbol: "AAPL",
        quantity: 100,
        avg_cost_basis: 150,
        current_price: 175,
        market_value: 17500,
        unrealized_pnl: 2500,
        unrealized_pnl_pct: 16.67,
        holding_period_days: 180,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("type-AAPL")).toHaveTextContent("stock");
    });

    it("displays option type for option positions", () => {
      const position: Position = {
        symbol: "AAPL_CALL",
        quantity: 10,
        avg_cost_basis: 5,
        current_price: 7.5,
        market_value: 750,
        unrealized_pnl: 250,
        unrealized_pnl_pct: 50,
        holding_period_days: 30,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "option",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("type-AAPL_CALL")).toHaveTextContent("option");
    });
  });

  describe("Edge Cases", () => {
    it("handles null current_price", () => {
      const position: Position = {
        symbol: "DELISTED",
        quantity: 100,
        avg_cost_basis: 50,
        current_price: null,
        market_value: null,
        unrealized_pnl: null,
        unrealized_pnl_pct: null,
        holding_period_days: 200,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("price-DELISTED")).toBeInTheDocument();
      expect(screen.getByTestId("pnl-DELISTED")).toBeInTheDocument();
    });

    it("handles zero quantity", () => {
      const position: Position = {
        symbol: "ZERO",
        quantity: 0,
        avg_cost_basis: 100,
        current_price: 100,
        market_value: 0,
        unrealized_pnl: 0,
        unrealized_pnl_pct: 0,
        holding_period_days: 100,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("qty-ZERO")).toHaveTextContent("0");
    });

    it("handles very large position values", () => {
      const position: Position = {
        symbol: "MEGA",
        quantity: 1000000,
        avg_cost_basis: 500,
        current_price: 600,
        market_value: 600000000,
        unrealized_pnl: 100000000,
        unrealized_pnl_pct: 20,
        holding_period_days: 365,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("qty-MEGA")).toHaveTextContent("1000000");
      expect(screen.getByTestId("value-MEGA")).toHaveTextContent("600000000");
    });

    it("handles null holding_period_days", () => {
      const position: Position = {
        symbol: "NEW",
        quantity: 50,
        avg_cost_basis: 100,
        current_price: 105,
        market_value: 5250,
        unrealized_pnl: 250,
        unrealized_pnl_pct: 5,
        holding_period_days: null,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("days-NEW")).toBeInTheDocument();
    });

    it("handles fractional shares", () => {
      const position: Position = {
        symbol: "FRAC",
        quantity: 0.125,
        avg_cost_basis: 100,
        current_price: 110,
        market_value: 13.75,
        unrealized_pnl: 1.25,
        unrealized_pnl_pct: 10,
        holding_period_days: 50,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      expect(screen.getByTestId("qty-FRAC")).toHaveTextContent("0.125");
    });

    it("handles very high loss percentage", () => {
      const position: Position = {
        symbol: "LOSS",
        quantity: 100,
        avg_cost_basis: 100,
        current_price: 10,
        market_value: 1000,
        unrealized_pnl: -9000,
        unrealized_pnl_pct: -90,
        holding_period_days: 180,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 0,
        earliest_purchase_date: "",
        tax_lots: [],
      };

      render(<PositionsTable positions={[position]} />);

      const row = screen.getByTestId("position-row-LOSS");
      expect(row).toHaveClass("loss-row");
    });
  });

  describe("Mixed Portfolio", () => {
    it("correctly displays a mixed portfolio with gains, losses, and breakeven", () => {
      const positions: Position[] = [
        {
          symbol: "WINNER",
          quantity: 100,
          avg_cost_basis: 50,
          current_price: 100,
          market_value: 10000,
          unrealized_pnl: 5000,
          unrealized_pnl_pct: 100,
          holding_period_days: 365,
          is_long_term: true,
          wash_sale_risk: false,
          asset_type: "stock",
          total_cost_basis: 0,
          earliest_purchase_date: "",
          tax_lots: [],
        },
        {
          symbol: "LOSER",
          quantity: 50,
          avg_cost_basis: 100,
          current_price: 50,
          market_value: 2500,
          unrealized_pnl: -2500,
          unrealized_pnl_pct: -50,
          holding_period_days: 90,
          is_long_term: false,
          wash_sale_risk: true,
          asset_type: "stock",
          total_cost_basis: 0,
          earliest_purchase_date: "",
          tax_lots: [],
        },
        {
          symbol: "BREAKEVEN",
          quantity: 200,
          avg_cost_basis: 75,
          current_price: 75,
          market_value: 15000,
          unrealized_pnl: 0,
          unrealized_pnl_pct: 0,
          holding_period_days: 180,
          is_long_term: true,
          wash_sale_risk: false,
          asset_type: "stock",
          total_cost_basis: 0,
          earliest_purchase_date: "",
          tax_lots: [],
        },
      ];

      const { container } = render(<PositionsTable positions={positions} />);

      const winnerRow = container.querySelector(
        '[data-testid="position-row-WINNER"]',
      );
      const loserRow = container.querySelector(
        '[data-testid="position-row-LOSER"]',
      );
      const breakevenRow = container.querySelector(
        '[data-testid="position-row-BREAKEVEN"]',
      );

      expect(winnerRow).toHaveClass("gain-row");
      expect(loserRow).toHaveClass("loss-row");
      expect(breakevenRow).not.toHaveClass("gain-row");
      expect(breakevenRow).not.toHaveClass("loss-row");
    });
  });

  describe("Tax lots detail panel", () => {
    it("expands lot details when a position row is clicked", () => {
      const position: Position = {
        symbol: "AAPL",
        quantity: 15,
        avg_cost_basis: 180,
        current_price: 190,
        market_value: 2850,
        unrealized_pnl: 150,
        unrealized_pnl_pct: 5.5,
        holding_period_days: 400,
        is_long_term: true,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 2700,
        earliest_purchase_date: "2024-07-01",
        tax_lots: [
          {
            symbol: "AAPL",
            quantity: 10,
            cost_basis_per_share: 185.5,
            total_cost_basis: 1855,
            purchase_date: "2024-07-01",
            current_price: 190,
            asset_type: "stock",
            unrealized_pnl: 45,
            unrealized_pnl_pct: 2.4,
            holding_period_days: 400,
            is_long_term: true,
            wash_sale_disallowed: 0,
          },
          {
            symbol: "AAPL",
            quantity: 5,
            cost_basis_per_share: 172.2,
            total_cost_basis: 861,
            purchase_date: "2025-03-10",
            current_price: 190,
            asset_type: "stock",
            unrealized_pnl: 89,
            unrealized_pnl_pct: 10.3,
            holding_period_days: 90,
            is_long_term: false,
            wash_sale_disallowed: 25.5,
          },
        ],
      };

      render(<PositionsTable positions={[position]} />);

      expect(
        screen.queryByTestId("tax-lots-panel-AAPL"),
      ).not.toBeInTheDocument();

      fireEvent.click(screen.getByTestId("position-row-AAPL"));

      expect(screen.getByTestId("tax-lots-panel-AAPL")).toBeInTheDocument();
      expect(screen.getByTestId("tax-lot-AAPL-0")).toHaveTextContent("10");
      expect(screen.getByTestId("tax-lot-AAPL-1")).toHaveTextContent("$25.50");
      expect(screen.getAllByText("ST").length).toBeGreaterThan(0);
      expect(screen.getAllByText("LT").length).toBeGreaterThan(0);
      expect(screen.getByTestId("tax-lot-AAPL-0")).toHaveTextContent("Jul 1, 2024");
    });

    it("formats YYYY-MM-DD lot dates as local calendar dates, not UTC midnight", () => {
      const position: Position = {
        symbol: "DATEBUG",
        quantity: 1,
        avg_cost_basis: 10,
        current_price: 12,
        market_value: 12,
        unrealized_pnl: 2,
        unrealized_pnl_pct: 20,
        holding_period_days: 10,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 10,
        earliest_purchase_date: "2024-01-15",
        tax_lots: [
          {
            symbol: "DATEBUG",
            quantity: 1,
            cost_basis_per_share: 10,
            total_cost_basis: 10,
            purchase_date: "2024-01-15",
            current_price: 12,
            asset_type: "stock",
            unrealized_pnl: 2,
            unrealized_pnl_pct: 20,
            holding_period_days: 10,
            is_long_term: false,
            wash_sale_disallowed: 0,
          },
        ],
      };

      render(<PositionsTable positions={[position]} />);
      fireEvent.click(screen.getByTestId("position-row-DATEBUG"));

      const expected = new Date(2024, 0, 15).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
      expect(expected).toBe("Jan 15, 2024");
      expect(screen.getByTestId("tax-lot-DATEBUG-0")).toHaveTextContent(
        "Jan 15, 2024",
      );
    });
  });

  describe("Wash-sale details on lot panel", () => {
    const amdReplacementLot: TaxLot = {
      symbol: "AMD",
      quantity: 10,
      cost_basis_per_share: 155,
      total_cost_basis: 1550,
      purchase_date: "2025-07-25",
      current_price: 130,
      asset_type: "stock",
      unrealized_pnl: -250,
      unrealized_pnl_pct: -16.1,
      holding_period_days: 20,
      is_long_term: false,
      wash_sale_disallowed: 300,
    };

    const amdOriginalLot: TaxLot = {
      symbol: "AMD",
      quantity: 4,
      cost_basis_per_share: 150,
      total_cost_basis: 600,
      purchase_date: "2025-06-01",
      current_price: 130,
      asset_type: "stock",
      unrealized_pnl: -80,
      unrealized_pnl_pct: -13.3,
      holding_period_days: 70,
      is_long_term: false,
      wash_sale_disallowed: 0,
    };

    const amdWashFlag: WashSaleFlag = {
      symbol: "AMD",
      sale_date: "2025-07-15",
      sale_quantity: 10,
      sale_loss: 300,
      repurchase_date: "2025-07-25",
      repurchase_quantity: 10,
      disallowed_loss: 300,
      adjusted_cost_basis: 1550,
      explanation:
        "Wash sale: Sold 10 AMD on 07/15/2025 at a loss of $300.00, then repurchased 10 shares on 07/25/2025.",
    };

    const amdPosition: Position = {
      symbol: "AMD",
      quantity: 14,
      avg_cost_basis: 153.57,
      current_price: 130,
      market_value: 1820,
      unrealized_pnl: -330,
      unrealized_pnl_pct: -15.3,
      holding_period_days: 70,
      is_long_term: false,
      wash_sale_risk: false,
      asset_type: "stock",
      total_cost_basis: 2150,
      earliest_purchase_date: "2025-06-01",
      tax_lots: [amdOriginalLot, amdReplacementLot],
    };

    it("shows disallowed loss, replacement basis bump, and 30-day window on the replacement lot", () => {
      render(
        <PositionsTable positions={[amdPosition]} washSaleFlags={[amdWashFlag]} />,
      );
      fireEvent.click(screen.getByTestId("position-row-AMD"));

      expect(screen.getByTestId("tax-lots-panel-AMD")).toBeInTheDocument();
      expect(screen.queryByTestId("tax-lot-wash-AMD-0")).not.toBeInTheDocument();
      expect(screen.getByTestId("tax-lot-AMD-0")).toHaveTextContent("—");

      const washRow = screen.getByTestId("tax-lot-wash-AMD-1");
      expect(washRow).toHaveTextContent("Disallowed loss $300.00");
      expect(washRow).toHaveTextContent("Replacement-lot basis bump +$300.00");
      expect(washRow).toHaveTextContent("30-day window Jun 15, 2025");
      expect(washRow).toHaveTextContent("Aug 14, 2025");
      expect(washRow).toHaveTextContent("Sold Jul 15, 2025");
      expect(washRow).toHaveTextContent("replaced Jul 25, 2025");
      expect(screen.getByTestId("tax-lot-AMD-1")).toHaveTextContent("$300.00");
    });

    it("keeps a clean lot list when no wash sale is present", () => {
      const msft: Position = {
        symbol: "MSFT",
        quantity: 8,
        avg_cost_basis: 415,
        current_price: 420,
        market_value: 3360,
        unrealized_pnl: 40,
        unrealized_pnl_pct: 1.2,
        holding_period_days: 80,
        is_long_term: false,
        wash_sale_risk: false,
        asset_type: "stock",
        total_cost_basis: 3320,
        earliest_purchase_date: "2025-06-02",
        tax_lots: [
          {
            symbol: "MSFT",
            quantity: 8,
            cost_basis_per_share: 415,
            total_cost_basis: 3320,
            purchase_date: "2025-06-02",
            current_price: 420,
            asset_type: "stock",
            unrealized_pnl: 40,
            unrealized_pnl_pct: 1.2,
            holding_period_days: 80,
            is_long_term: false,
            wash_sale_disallowed: 0,
          },
        ],
      };

      render(<PositionsTable positions={[msft]} washSaleFlags={[]} />);
      fireEvent.click(screen.getByTestId("position-row-MSFT"));

      expect(screen.getByTestId("tax-lots-panel-MSFT")).toBeInTheDocument();
      expect(screen.getByTestId("tax-lot-MSFT-0")).toBeInTheDocument();
      expect(screen.queryByTestId("tax-lot-wash-MSFT-0")).not.toBeInTheDocument();
      expect(screen.queryByText(/Disallowed loss/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/30-day window/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/Replacement-lot basis bump/i)).not.toBeInTheDocument();
      expect(screen.getByTestId("tax-lot-MSFT-0")).toHaveTextContent("Jun 2, 2025");
    });
  });
});
