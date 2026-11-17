import { fireEvent, render, screen } from "@testing-library/react";
import AccountExportsField from "../../app/components/AccountExportsField";

describe("AccountExportsField", () => {
  it("asks for two labeled exports before analyze", () => {
    const onAnalyze = jest.fn();
    render(<AccountExportsField onAnalyze={onAnalyze} />);

    const button = screen.getByTestId("analyze-accounts");
    expect(button).toBeDisabled();

    const individual = new File(["a"], "individual.csv", { type: "text/csv" });
    const ira = new File(["b"], "ira.csv", { type: "text/csv" });
    fireEvent.change(screen.getByTestId("account-exports-input"), {
      target: { files: [individual, ira] },
    });

    expect(screen.getByText("individual.csv")).toBeInTheDocument();
    expect(screen.getByText("ira.csv")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("account-label-1"), {
      target: { value: "ira_traditional" },
    });
    expect(button).toBeEnabled();
    fireEvent.click(button);

    expect(onAnalyze).toHaveBeenCalledWith([
      { file: individual, label: "individual" },
      { file: ira, label: "ira_traditional" },
    ]);
  });
});
