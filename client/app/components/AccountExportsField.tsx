"use client";

import { useId, useState } from "react";
import { Button, Stack, Typography } from "@mui/material";
import {
  ACCOUNT_LABEL_OPTIONS,
  type AccountExport,
  type AccountLabel,
  isAccountLabel,
} from "@/lib/accountLabels";

export default function AccountExportsField({
  disabled = false,
  onAnalyze,
}: Readonly<{
  disabled?: boolean;
  onAnalyze: (exports: AccountExport[]) => void;
}>) {
  const inputId = useId();
  const [exports, setExports] = useState<AccountExport[]>([]);

  const addFiles = (list: FileList | null) => {
    if (!list || list.length === 0) return;
    const next = Array.from(list).map((file) => ({
      file,
      label: "individual" as AccountLabel,
    }));
    setExports((current) => [...current, ...next].slice(0, 6));
  };

  return (
    <Stack spacing={1} data-testid="account-exports">
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        More than one Robinhood account
      </Typography>
      <Typography variant="caption" color="text.secondary">
        Add 2 or more CSV exports for the same tax year. Label each one
        Individual, IRA (traditional or Roth), or Other/agentic. Exports only.
      </Typography>
      <input
        id={inputId}
        data-testid="account-exports-input"
        className="file-input-hidden"
        type="file"
        accept=".csv"
        multiple
        disabled={disabled}
        onChange={(event) => {
          addFiles(event.target.files);
          event.target.value = "";
        }}
      />
      <Button
        component="label"
        htmlFor={inputId}
        variant="outlined"
        size="small"
        disabled={disabled}
        data-testid="account-exports-add"
        sx={{ alignSelf: "flex-start", textTransform: "none" }}
      >
        Add account exports
      </Button>
      {exports.map((item, index) => (
        <Stack
          key={`${item.file.name}-${item.file.size}-${index}`}
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          sx={{ alignItems: { sm: "center" } }}
        >
          <Typography variant="body2" sx={{ minWidth: 0, overflowWrap: "anywhere" }}>
            {item.file.name}
          </Typography>
          <select
            data-testid={`account-label-${index}`}
            aria-label={`Account label for ${item.file.name}`}
            value={item.label}
            disabled={disabled}
            onChange={(event) => {
              const value = event.target.value;
              if (!isAccountLabel(value)) return;
              setExports((current) =>
                current.map((row, rowIndex) =>
                  rowIndex === index ? { ...row, label: value } : row,
                ),
              );
            }}
            style={{ font: "inherit", padding: "4px 8px", maxWidth: "100%" }}
          >
            {ACCOUNT_LABEL_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <Button
            size="small"
            disabled={disabled}
            onClick={() =>
              setExports((current) => current.filter((_, rowIndex) => rowIndex !== index))
            }
            sx={{ textTransform: "none" }}
          >
            Remove
          </Button>
        </Stack>
      ))}
      <Button
        variant="contained"
        size="small"
        data-testid="analyze-accounts"
        disabled={disabled || exports.length < 2}
        onClick={() => onAnalyze(exports)}
        sx={{ alignSelf: "flex-start", textTransform: "none" }}
      >
        Analyze these accounts
      </Button>
    </Stack>
  );
}
