import { readFileSync } from "fs";
import path from "path";
import { render, screen } from "@testing-library/react";
import WashSaleWarning from "../../app/components/WashSaleWarning";
import type {
  WashSaleChain,
  WashSaleFlag,
  WashSaleHop,
  WashReconcileStatus,
} from "../../lib/types";

interface MatrixFlag {
  symbol: string;
  sale_date: string;
  sale_quantity: number;
  disallowed_loss: number;
  repurchase_date?: string;
  repurchase_quantity?: number;
  adjusted_cost_basis?: number;
}

interface MatrixChain {
  symbol: string;
  sale_date?: string;
  sale_quantity?: number;
  disallowed_loss?: number;
  reconcile_status?: WashReconcileStatus;
  not_compared_reason?: string;
  cross_account?: boolean;
  plain_english?: string | null;
}

interface MatrixRow {
  id: string;
  inputs: {
    source: string;
    synthetic_flag?: MatrixFlag;
    component_flags?: MatrixFlag[];
    component_chains?: MatrixChain[];
  };
  expected: {
    same_account_flags: { items: MatrixFlag[] };
    same_account_chains: MatrixChain[];
    cross_account_chains: MatrixChain[];
    radar: {
      total: number;
      event_count: number;
      tickers: string[];
      visible: boolean;
    };
    locked_teaser: {
      shows_radar: boolean;
      tickers: string[];
    };
  };
}

const matrix = JSON.parse(
  readFileSync(
    path.join(
      __dirname,
      "../../../server/tests/fixtures/wash-sale-scenario-matrix.json",
    ),
    "utf8",
  ),
) as { rows: MatrixRow[] };

function asFlag(item: MatrixFlag): WashSaleFlag {
  return {
    symbol: item.symbol,
    sale_date: item.sale_date,
    sale_quantity: item.sale_quantity,
    sale_loss: item.disallowed_loss,
    repurchase_date: item.repurchase_date ?? item.sale_date,
    repurchase_quantity: item.repurchase_quantity ?? item.sale_quantity,
    disallowed_loss: item.disallowed_loss,
    adjusted_cost_basis: item.adjusted_cost_basis ?? 0,
    explanation: "Replacement shares",
  };
}

function asChain(spec: MatrixChain, index: number): WashSaleChain | null {
  if (!spec.sale_date || spec.sale_quantity == null || spec.disallowed_loss == null) {
    return null;
  }
  const hop: WashSaleHop = {
    hop_id: `${spec.symbol}-${index}`,
    hop_index: 0,
    symbol: spec.symbol,
    sale_date: spec.sale_date,
    sale_quantity: spec.sale_quantity,
    proceeds: 0,
    cost_basis: 0,
    loss: spec.disallowed_loss,
    replacement_buys: [],
    disallowed_loss: spec.disallowed_loss,
    plain_english: spec.plain_english ?? "",
    reconcile_status: spec.reconcile_status ?? "not_compared",
    not_compared_reason: spec.not_compared_reason ?? "",
    cross_account: spec.cross_account ?? false,
  };
  return {
    chain_id: `${spec.symbol}-${index}`,
    symbol: spec.symbol,
    reconcile_status: hop.reconcile_status,
    plain_english: hop.plain_english,
    cross_account: spec.cross_account ?? false,
    hops: [hop],
  };
}

function propsFor(row: MatrixRow): { flags: WashSaleFlag[]; chains: WashSaleChain[] } {
  if (row.inputs.source === "component_props") {
    return {
      flags: (row.inputs.component_flags ?? []).map(asFlag),
      chains: (row.inputs.component_chains ?? [])
        .map(asChain)
        .filter((chain): chain is WashSaleChain => chain !== null),
    };
  }
  if (row.inputs.synthetic_flag) {
    const flag = asFlag(row.inputs.synthetic_flag);
    return {
      flags: [flag],
      chains: row.expected.same_account_chains
        .map(asChain)
        .filter((chain): chain is WashSaleChain => chain !== null),
    };
  }
  const chains = [
    ...row.expected.same_account_chains,
    ...row.expected.cross_account_chains,
  ]
    .map(asChain)
    .filter((chain): chain is WashSaleChain => chain !== null);
  return {
    flags: row.expected.same_account_flags.items.map(asFlag),
    chains,
  };
}

function money(amount: number): string {
  return amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

describe("wash-sale scenario matrix radar", () => {
  it.each(matrix.rows)("$id", (row) => {
    const { flags, chains } = propsFor(row);
    const unlocked = render(
      <WashSaleWarning flags={flags} chains={chains} />,
    );
    const radar = row.expected.radar;
    if (!radar.visible) {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    } else {
      const noun = radar.event_count === 1 ? "event" : "events";
      const tickerNoun = radar.tickers.length === 1 ? "ticker" : "tickers";
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent(
        `Wash-sale radar (${radar.event_count} ${noun} across ${radar.tickers.length} ${tickerNoun})`,
      );
      expect(alert).toHaveTextContent(
        `Total disallowed loss: $${money(radar.total)}`,
      );
      for (const ticker of radar.tickers) {
        expect(screen.getByText(ticker)).toBeInTheDocument();
      }
    }
    unlocked.unmount();

    const teaser = row.expected.locked_teaser;
    render(<WashSaleWarning flags={flags} chains={chains} locked />);
    if (!teaser.shows_radar) {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      return;
    }
    expect(screen.getByTestId("wash-sale-packet-preview")).toBeInTheDocument();
    for (const ticker of teaser.tickers) {
      expect(screen.getByText(ticker)).toBeInTheDocument();
    }
    expect(screen.queryByText(/Total disallowed loss/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\$[\d,.]+ disallowed/i)).not.toBeInTheDocument();
  });
});
