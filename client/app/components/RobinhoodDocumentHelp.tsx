"use client";

import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import {
  ArrowDownward as ArrowDownwardIcon,
  ArrowForward as ArrowForwardIcon,
  Close as CloseIcon,
  Description as DescriptionIcon,
  HelpOutline as HelpOutlineIcon,
  OpenInNew as OpenInNewIcon,
  PictureAsPdf as PictureAsPdfIcon,
} from "@mui/icons-material";

type GuideType = "csv" | "pdf";

type GuideStep = {
  title: string;
  detail: string;
  callout?: string;
};

type GuideConfig = {
  title: string;
  subtitle: string;
  badge: string;
  icon: React.ReactNode;
  steps: GuideStep[];
};

const GUIDE_CONTENT: Record<GuideType, GuideConfig> = {
  csv: {
    title: "Robinhood CSV export",
    subtitle:
      "Use this when you want your transaction history for the portfolio analysis upload.",
    badge: "Portfolio analysis input",
    icon: <DescriptionIcon color="primary" />,
    steps: [
      {
        title: "Open Robinhood on the web",
        detail:
          "Sign in at Robinhood.com from a desktop browser. The export flow is typically easier to find on web than in the mobile app.",
        callout:
          "If you start on mobile, look for Account → Menu → Statements & History.",
      },
      {
        title: "Go to your account history or statements area",
        detail:
          "Open the account menu, then navigate to History, Reports, Tax Center, or Statements depending on Robinhood’s current layout.",
        callout:
          "Robinhood occasionally renames this section, so use the search/help menu if the label looks different.",
      },
      {
        title: "Export the activity as CSV",
        detail:
          "Look for an Export, Download CSV, or account activity download action and save the file to your computer.",
        callout:
          "Choose the broadest date range available so the analysis can reconstruct positions more accurately.",
      },
      {
        title: "Return here and upload the file",
        detail:
          "Back in OptionsTaxHub, click the CSV upload area and choose the downloaded export.",
      },
    ],
  },
  pdf: {
    title: "Robinhood 1099 PDF",
    subtitle:
      "Use this optional supplement when you want prior-year broker-reported basis and wash-sale detail for manual reconciliation.",
    badge: "Optional PDF supplement",
    icon: <PictureAsPdfIcon color="error" />,
    steps: [
      {
        title: "Open Robinhood Tax Documents",
        detail:
          "Sign in to Robinhood on the web and open the Tax Center or Documents area from the account menu.",
        callout:
          "The PDF is usually listed under tax documents for the prior filing year.",
      },
      {
        title: "Find the consolidated 1099",
        detail:
          "Choose the Robinhood 1099, Composite 1099, or 1099-B style document for the previous tax year requested by the app.",
        callout:
          "If there are corrected copies, prefer the newest corrected PDF.",
      },
      {
        title: "Download the PDF to your computer",
        detail:
          "Save the full PDF locally without renaming it unless you want a clearer filename for your records.",
      },
      {
        title: "Attach it in the 1099 PDF panel",
        detail:
          "Use the Add 1099 PDF button in the dashboard. If you already uploaded a CSV, the app will automatically re-run the analysis with the supplement.",
      },
    ],
  },
};

function GuideStepCard({
  index,
  step,
}: Readonly<{
  index: number;
  step: GuideStep;
}>) {
  return (
    <Box
      sx={{
        flex: 1,
        minWidth: { xs: "100%", md: 180 },
        borderRadius: 3,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        p: 2,
      }}
    >
      <Stack spacing={1}>
        <Chip
          label={`Step ${index + 1}`}
          size="small"
          color="primary"
          sx={{ alignSelf: "flex-start", fontWeight: 700 }}
        />
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
          {step.title}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {step.detail}
        </Typography>
        {step.callout && (
          <Box
            sx={{
              borderRadius: 2,
              bgcolor: "action.hover",
              px: 1.25,
              py: 1,
            }}
          >
            <Typography variant="caption" color="text.secondary">
              {step.callout}
            </Typography>
          </Box>
        )}
      </Stack>
    </Box>
  );
}

export default function RobinhoodDocumentHelp() {
  const [open, setOpen] = useState(false);
  const [guideType, setGuideType] = useState<GuideType>("csv");

  const activeGuide = useMemo(() => GUIDE_CONTENT[guideType], [guideType]);

  return (
    <>
      <Box
        sx={{
          borderRadius: 3,
          border: "1px solid",
          borderColor: "info.light",
          bgcolor: "rgba(227, 242, 253, 0.55)",
          p: 2,
        }}
      >
        <Stack
          direction={{ xs: "column", md: "row" }}
          spacing={1.5}
          alignItems={{ xs: "flex-start", md: "center" }}
          justifyContent="space-between"
        >
          <Stack spacing={0.5}>
            <Stack direction="row" spacing={1} alignItems="center">
              <HelpOutlineIcon color="info" fontSize="small" />
              <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                Need help retrieving your Robinhood files?
              </Typography>
            </Stack>
            <Typography variant="body2" color="text.secondary">
              We can walk you through getting both the CSV export and the
              prior-year 1099 PDF before you upload anything.
            </Typography>
          </Stack>
          <Button
            variant="outlined"
            size="small"
            onClick={() => setOpen(true)}
            endIcon={<OpenInNewIcon />}
            sx={{ whiteSpace: "nowrap" }}
          >
            Show retrieval steps
          </Button>
        </Stack>
      </Box>

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        fullWidth
        maxWidth="lg"
        slotProps={{
          paper: {
            sx: {
              borderRadius: 4,
              overflow: "hidden",
            },
          },
        }}
      >
        <Box
          sx={{
            background:
              "linear-gradient(135deg, #0d47a1 0%, #1565c0 55%, #42a5f5 100%)",
            color: "common.white",
            px: { xs: 2, sm: 3 },
            py: 2.5,
            position: "relative",
          }}
        >
          <IconButton
            onClick={() => setOpen(false)}
            sx={{
              position: "absolute",
              top: 10,
              right: 10,
              color: "common.white",
              "&:hover": { bgcolor: "rgba(255,255,255,0.12)" },
            }}
            aria-label="close Robinhood document help"
          >
            <CloseIcon />
          </IconButton>
          <Stack spacing={1} sx={{ pr: 5 }}>
            <DialogTitle sx={{ p: 0, color: "inherit", fontWeight: 800 }}>
              Robinhood document retrieval guide
            </DialogTitle>
            <Typography variant="body2" sx={{ opacity: 0.9, maxWidth: 720 }}>
              Pick the document you need, then follow the step-by-step path
              below. This flow is designed to help users who have not downloaded
              brokerage files before.
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <Button
                variant={guideType === "csv" ? "contained" : "outlined"}
                color={guideType === "csv" ? "secondary" : "inherit"}
                onClick={() => setGuideType("csv")}
                startIcon={<DescriptionIcon />}
                sx={{
                  textTransform: "none",
                  borderColor: "rgba(255,255,255,0.55)",
                  color: guideType === "csv" ? undefined : "common.white",
                }}
              >
                CSV export
              </Button>
              <Button
                variant={guideType === "pdf" ? "contained" : "outlined"}
                color={guideType === "pdf" ? "secondary" : "inherit"}
                onClick={() => setGuideType("pdf")}
                startIcon={<PictureAsPdfIcon />}
                sx={{
                  textTransform: "none",
                  borderColor: "rgba(255,255,255,0.55)",
                  color: guideType === "pdf" ? undefined : "common.white",
                }}
              >
                1099 PDF
              </Button>
            </Stack>
          </Stack>
        </Box>

        <DialogContent sx={{ px: { xs: 2, sm: 3 }, py: 3 }}>
          <Stack spacing={2.5}>
            <Stack
              direction={{ xs: "column", md: "row" }}
              spacing={1.5}
              justifyContent="space-between"
              alignItems={{ xs: "flex-start", md: "center" }}
            >
              <Stack spacing={0.75}>
                <Stack direction="row" spacing={1} alignItems="center">
                  {activeGuide.icon}
                  <Typography variant="h6" sx={{ fontWeight: 700 }}>
                    {activeGuide.title}
                  </Typography>
                  <Chip
                    label={activeGuide.badge}
                    size="small"
                    color="primary"
                    variant="outlined"
                  />
                </Stack>
                <Typography variant="body2" color="text.secondary">
                  {activeGuide.subtitle}
                </Typography>
              </Stack>
              <Typography variant="caption" color="text.secondary">
                Robinhood can change menu labels occasionally, but the document
                lives in the same account, tax, or statements areas.
              </Typography>
            </Stack>

            <Stack
              direction={{ xs: "column", md: "row" }}
              spacing={{ xs: 1, md: 0 }}
              alignItems="stretch"
            >
              {activeGuide.steps.map((step, index) => (
                <Stack
                  key={step.title}
                  direction={{ xs: "column", md: "row" }}
                  spacing={1}
                  alignItems="center"
                  sx={{ flex: 1 }}
                >
                  <GuideStepCard index={index} step={step} />
                  {index < activeGuide.steps.length - 1 && (
                    <Box
                      sx={{
                        color: "primary.main",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        px: { md: 1 },
                        py: { xs: 0.5, md: 0 },
                      }}
                      aria-hidden="true"
                    >
                      <ArrowForwardIcon
                        sx={{ display: { xs: "none", md: "block" } }}
                      />
                      <ArrowDownwardIcon
                        sx={{ display: { xs: "block", md: "none" } }}
                      />
                    </Box>
                  )}
                </Stack>
              ))}
            </Stack>

            <Box
              sx={{
                borderRadius: 3,
                bgcolor: "grey.50",
                border: "1px solid",
                borderColor: "divider",
                p: 2,
              }}
            >
              <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
                What to do next in OptionsTaxHub
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Upload the CSV first for a full portfolio analysis. Add the 1099
                PDF when you want extra context for basis carryovers, wash-sale
                adjustments, assignments, or renamed tickers.
              </Typography>
            </Box>
          </Stack>
        </DialogContent>
      </Dialog>
    </>
  );
}
