import { render, screen } from "@testing-library/react";
import PositionsTable from "../../app/components/PositionsTable";
import type { Position } from "@/lib/types";

// Mock MUI DataGrid to avoid license warnings and simplify testing
jest.mock("@mui/x-data-grid", () => ({
  DataGrid: ({
    rows,
    columns,
    getRowClassName,
  }: Readonly<Record<string, unknown>>) => {
    const rowsArray = Array.isArray(rows) ? rows : [];
    return (
      <div data-testid="positions-table">
        {rowsArray.map((row: unknown) => {
          const position = row as Position;
          const rowClass =
            (getRowClassName as (params: Record<string, unknown>) => string)?.({
              row: position,
            }) || "";
          return (
            <div
              key={position.symbol}
              data-testid={`position-row-${position.symbol}`}
              className={rowClass}
              data-pnl={position.unrealized_pnl}
            >
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
});
