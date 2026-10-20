"use client";

import { Box, Button, Stack, Typography } from "@mui/material";

export const SAVED_SAMPLE_FILENAME = "sample-robinhood-transactions.csv";

export function formatLatestRunDate(uploadedAt: string): string {
  const date = new Date(uploadedAt);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function latestRunBarText(
  filename: string,
  uploadedAt: string,
  sampleRun: boolean,
): string {
  const trimmed = filename.trim();
  const runName =
    sampleRun || trimmed === SAVED_SAMPLE_FILENAME
      ? "sample"
      : trimmed || "saved run";
  const dateLabel = formatLatestRunDate(uploadedAt);
  if (!dateLabel) {
    return `Showing your latest run: ${runName}`;
  }
  return `Showing your latest run: ${runName} · ${dateLabel}`;
}

/**
 * One bar for the auto-opened newest saved run. The same component stacks
 * its actions at phone width and sits on one row from the sm breakpoint up.
 */
export default function LatestRunBar({
  filename,
  uploadedAt,
  sampleRun,
  onUploadNew,
  onAllSavedRuns,
}: Readonly<{
  filename: string;
  uploadedAt: string;
  sampleRun: boolean;
  onUploadNew: () => void;
  onAllSavedRuns: () => void;
}>) {
  return (
    <Box
      data-testid="latest-run-bar"
      role="region"
      aria-label="Latest saved run"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: { xs: 1.5, sm: 2 },
        py: 1.5,
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1.5}
        sx={{
          alignItems: { xs: "stretch", sm: "center" },
          justifyContent: "space-between",
        }}
      >
        <Typography
          variant="body2"
          sx={{ fontWeight: 600, minWidth: 0, overflowWrap: "anywhere" }}
        >
          {latestRunBarText(filename, uploadedAt, sampleRun)}
        </Typography>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          sx={{ flexShrink: 0 }}
        >
          <Button
            variant="contained"
            data-testid="latest-run-upload-new"
            onClick={onUploadNew}
            sx={{ minHeight: 44, width: { xs: "100%", sm: "auto" } }}
          >
            Upload new
          </Button>
          <Button
            variant="outlined"
            data-testid="latest-run-all-saved"
            onClick={onAllSavedRuns}
            sx={{ minHeight: 44, width: { xs: "100%", sm: "auto" } }}
          >
            All saved runs
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}
