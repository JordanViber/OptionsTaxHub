export type AccountLabel =
  | "individual"
  | "ira_traditional"
  | "ira_roth"
  | "other";

export const ACCOUNT_LABEL_OPTIONS: ReadonlyArray<{
  value: AccountLabel;
  label: string;
}> = [
  { value: "individual", label: "Individual" },
  { value: "ira_traditional", label: "IRA (traditional)" },
  { value: "ira_roth", label: "IRA (Roth)" },
  { value: "other", label: "Other/agentic" },
];

export interface AccountExport {
  file: File;
  label: AccountLabel;
}

export function isAccountLabel(value: string): value is AccountLabel {
  return ACCOUNT_LABEL_OPTIONS.some((option) => option.value === value);
}
