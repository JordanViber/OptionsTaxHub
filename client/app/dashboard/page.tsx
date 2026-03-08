"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Accordion,
  AccordionSummary,
  AccordionDetails,
  AppBar,
  Toolbar,
  Container,
  Box,
  Card,
  CardContent,
  Button,
  Chip,
  CircularProgress,
  Alert,
  AlertTitle,
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Snackbar,
  Stack,
  Typography,
  Menu,
  MenuItem,
  Avatar,
  Tabs,
  Tab,
  LinearProgress,
  Divider,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
} from "@mui/material";
import {
  CloudUpload as CloudUploadIcon,
  Error as ErrorIcon,
  ExpandMore as ExpandMoreIcon,
  Logout as LogoutIcon,
  Settings as SettingsIcon,
  Dashboard as DashboardIcon,
  History as HistoryIcon,
  Favorite as HeartIcon,
  Close as CloseIcon,
  CalendarToday as CalendarIcon,
} from "@mui/icons-material";
import ServiceWorkerRegistration from "../components/ServiceWorkerRegistration";
import TaxDisclaimer from "../components/TaxDisclaimer";
import PortfolioSummaryCards from "../components/PortfolioSummaryCards";
import PositionsTable from "../components/PositionsTable";
import HarvestingSuggestions from "../components/HarvestingSuggestions";
import WashSaleWarning from "../components/WashSaleWarning";
import TipJar from "../components/TipJar";
import {
  useAnalyzePortfolio,
  useTaxProfile,
  usePortfolioHistory,
  useBackendHealth,
  fetchAnalysisById,
  cleanupOrphanHistory,
  deleteAnalysis,
} from "@/lib/api";
import { useAuth } from "@/app/context/auth";
import { useQueryClient } from "@tanstack/react-query";
import type {
  PortfolioAnalysis,
  AnalysisHistoryItem,
  Position,
} from "@/lib/types";

export const dynamic = "force-dynamic";

type AnalysisSource = "fresh-upload" | "saved-history" | "restored-session";

type DeleteTarget = {
  id: string;
  filename: string;
};

function buildPositionId(position: Position, index: number): string {
  return (
    position.position_id ??
    [
      position.symbol,
      position.asset_type,
      position.earliest_purchase_date ?? "unknown",
      index,
    ].join(":")
  );
}

function normalizeAnalysis(analysis: PortfolioAnalysis): PortfolioAnalysis {
  return {
    ...analysis,
    positions: analysis.positions.map((position, index) => ({
      ...position,
      position_id: buildPositionId(position, index),
    })),
  };
}

function getDisplayedAnalysis(
  loadedAnalysis: PortfolioAnalysis | null,
  analysis: PortfolioAnalysis | undefined,
): PortfolioAnalysis | null {
  if (loadedAnalysis) {
    return normalizeAnalysis(loadedAnalysis);
  }

  if (analysis) {
    return normalizeAnalysis(analysis);
  }

  return null;
}

function getAnalysisErrorMessage(error: unknown): string {
  if (!(error instanceof Error)) {
    return "An error occurred";
  }

  if (/failed to fetch|network|econnrefused/i.test(error.message)) {
    return "Could not reach the backend server. Make sure it is running on port 8011 (npm run dev:server).";
  }

  return error.message;
}

// Helper function: restore analysis from session storage
function restoreAnalysisFromStorage(): PortfolioAnalysis | null {
  try {
    const saved = sessionStorage.getItem("optionstaxhub-analysis");
    return saved
      ? normalizeAnalysis(JSON.parse(saved) as PortfolioAnalysis)
      : null;
  } catch {
    return null;
  }
}

// Helper function: save analysis to session storage
function saveAnalysisToStorage(analysis: PortfolioAnalysis): void {
  try {
    sessionStorage.setItem("optionstaxhub-analysis", JSON.stringify(analysis));
  } catch {
    // Storage full — ignore
  }
}

function getAnalysisSourceDisplay(source: AnalysisSource): {
  label: string;
  color: "success" | "info" | "default";
} {
  switch (source) {
    case "fresh-upload":
      return { label: "Fresh upload", color: "success" };
    case "saved-history":
      return { label: "Saved analysis", color: "info" };
    case "restored-session":
      return { label: "Restored from browser", color: "default" };
  }
}

function getConfidenceSummary(analysis: PortfolioAnalysis): {
  severity: "success" | "info" | "warning";
  label: string;
  summary: string;
  details: string[];
} {
  const warnings = analysis.warnings ?? [];
  const details: string[] = [];

  if (
    warnings.some((warning) =>
      /had no open lots at all|matched the required asset type|exceeded the available open lot quantity/i.test(
        warning,
      ),
    )
  ) {
    details.push(
      "Some sells could not be matched to complete tax lots, so related gains or losses were excluded.",
    );
  }

  if (
    warnings.some((warning) =>
      /Corporate action activity|stock split|share position may need manual verification/i.test(
        warning,
      ),
    )
  ) {
    details.push(
      "Unsupported brokerage events may still affect share counts or current position accuracy.",
    );
  }

  if (
    warnings.some((warning) =>
      /(^Row )|(CSV row\(s\) could not be parsed)/i.test(warning),
    )
  ) {
    details.push(
      "Some CSV rows could not be parsed and were excluded from the analysis.",
    );
  }

  if (
    warnings.some((warning) => /Live prices were unavailable/i.test(warning))
  ) {
    details.push(
      "Some current prices came from the CSV instead of a live market quote.",
    );
  }

  if (details.length === 0) {
    return {
      severity: "success",
      label: "High confidence",
      summary:
        "No major data quality issues were detected in this result. You can use the suggestions as a strong first pass for this tax year.",
      details: [],
    };
  }

  if (details.length === 1 && /live market quote/i.test(details[0])) {
    return {
      severity: "info",
      label: "Moderate confidence",
      summary:
        "This result is usable, but a small part of the valuation relied on fallback pricing instead of a live quote.",
      details,
    };
  }

  return {
    severity: "warning",
    label: "Partial confidence",
    summary:
      "Use these results as a starting point, but verify the affected holdings before acting on the analysis.",
    details,
  };
}

function getRecommendedNextSteps(analysis: PortfolioAnalysis): string[] {
  const steps: string[] = [];

  if (analysis.suggestions.length > 0) {
    steps.push(
      `Start with the ${analysis.suggestions.length} harvesting suggestion${analysis.suggestions.length === 1 ? "" : "s"} shown below.`,
    );
  } else {
    steps.push(
      "No obvious harvesting candidates were found, so review your open positions and realized gains for this tax year first.",
    );
  }

  if (analysis.wash_sale_flags.length > 0) {
    steps.push(
      "Review the wash-sale panel before relying on losses from affected symbols.",
    );
  }

  if (analysis.warnings.length > 0) {
    steps.push(
      "After that, check the data quality notes for any positions that may need manual verification.",
    );
  }

  return steps;
}

function getSkippedSuggestionSymbols(analysis: PortfolioAnalysis): string[] {
  const symbols = new Set<string>();
  const skippedSuggestionPattern =
    /^Skipped automated harvesting suggestions for (\S+) stock lots/i;

  for (const warning of analysis.warnings ?? []) {
    const match = skippedSuggestionPattern.exec(warning);
    if (match?.[1]) {
      symbols.add(match[1]);
    }
  }

  return Array.from(symbols).sort((left, right) => left.localeCompare(right));
}

function getSkippedSuggestionSymbolsForAnalysis(
  analysis: PortfolioAnalysis | null,
): string[] {
  if (!analysis) {
    return [];
  }

  return getSkippedSuggestionSymbols(analysis);
}

function SuggestionsPanel({
  suggestions,
  skippedSuggestionSymbols,
}: Readonly<{
  suggestions: PortfolioAnalysis["suggestions"];
  skippedSuggestionSymbols: string[];
}>) {
  return (
    <Stack spacing={2}>
      {skippedSuggestionSymbols.length > 0 && (
        <Alert severity="warning" variant="outlined">
          <AlertTitle>Manual review needed</AlertTitle>
          Automated harvesting suggestions were skipped for{" "}
          {skippedSuggestionSymbols.join(", ")} because a split or corporate
          action changed the reported share count. Review those symbols manually
          before acting.
        </Alert>
      )}
      <HarvestingSuggestions suggestions={suggestions} />
    </Stack>
  );
}

async function deleteHistoryEntry({
  userId,
  deleteTarget,
  invalidateHistory,
  clearDeleteTarget,
}: {
  userId: string | undefined;
  deleteTarget: DeleteTarget | null;
  invalidateHistory: () => void;
  clearDeleteTarget: () => void;
}): Promise<void> {
  if (!userId || !deleteTarget) {
    return;
  }

  try {
    await deleteAnalysis(deleteTarget.id);
    invalidateHistory();
  } catch (err) {
    console.error("Failed to delete analysis:", err);
  } finally {
    clearDeleteTarget();
  }
}

// Helper component: render history list or empty/error state
function HistoryContent({
  history,
  historyError,
  historyLoading,
  onItemClick,
  onDeleteClick,
}: Readonly<{
  history: AnalysisHistoryItem[] | null | undefined;
  historyError: Error | null;
  historyLoading: boolean;
  onItemClick: (id: string, filename: string) => void;
  onDeleteClick: (id: string, filename: string) => void;
}>) {
  if (historyError) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        <AlertTitle>Failed to Load History</AlertTitle>
        {historyError instanceof Error
          ? historyError.message
          : "Unable to load your past uploads. Please try refreshing the page."}
      </Alert>
    );
  }

  if (!history || history.length === 0) {
    return (
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ p: 2, textAlign: "center" }}
      >
        No past uploads yet. Upload a CSV to get started.
      </Typography>
    );
  }

  return (
    <List dense sx={{ overflow: "auto", maxHeight: "calc(100vh - 80px)" }}>
      {history.map((item) => (
        <ListItem
          key={item.id}
          disablePadding
          secondaryAction={
            <IconButton
              edge="end"
              size="small"
              aria-label="delete"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteClick(item.id, item.filename);
              }}
              sx={{
                opacity: 0.5,
                "&:hover": { opacity: 1, color: "error.main" },
              }}
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          }
        >
          <ListItemButton
            onClick={() => onItemClick(item.id, item.filename)}
            disabled={historyLoading}
            sx={{ pr: 5 }}
          >
            <ListItemText
              primary={item.filename}
              secondary={
                <>
                  {new Date(item.uploaded_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                  {" · "}
                  {item.positions_count} positions
                  {" · "}
                  {new Intl.NumberFormat("en-US", {
                    style: "currency",
                    currency: "USD",
                    maximumFractionDigits: 0,
                  }).format(item.total_market_value)}
                </>
              }
            />
          </ListItemButton>
        </ListItem>
      ))}
    </List>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { user, loading: authLoading, signOut } = useAuth();
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);
  const [activeTab, setActiveTab] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [tipJarOpen, setTipJarOpen] = useState(false);
  const [loadedAnalysis, setLoadedAnalysis] =
    useState<PortfolioAnalysis | null>(null);
  const [analysisSource, setAnalysisSource] = useState<AnalysisSource | null>(
    null,
  );
  const [historyLoading, setHistoryLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [snackbar, setSnackbar] = useState<{
    message: string;
    severity: "success" | "error" | "info";
  } | null>(null);
  const queryClient = useQueryClient();

  // Load the user's tax profile for analyze params
  const { data: taxProfile } = useTaxProfile({
    enabled: !!user,
    userId: user?.id,
  });

  // Backend health check — shows a banner when the API server is unreachable
  const { isError: backendDown, isFetched: backendChecked } =
    useBackendHealth();

  // Load past upload history
  const { data: history, error: historyError } = usePortfolioHistory(user?.id);

  // Full portfolio analysis mutation
  const {
    mutate: analyzePortfolio,
    isPending,
    error,
    data: analysis,
  } = useAnalyzePortfolio();

  // --- State persistence: restore analysis from sessionStorage on mount ---
  useEffect(() => {
    const saved = restoreAnalysisFromStorage();
    if (saved) {
      setLoadedAnalysis(saved);
      setAnalysisSource("restored-session");
    }
  }, []);

  // --- State persistence: save displayedAnalysis whenever it changes ---
  const displayedAnalysis = getDisplayedAnalysis(loadedAnalysis, analysis);
  const skippedSuggestionSymbols =
    getSkippedSuggestionSymbolsForAnalysis(displayedAnalysis);
  useEffect(() => {
    if (displayedAnalysis) {
      saveAnalysisToStorage(displayedAnalysis);
    }
  }, [displayedAnalysis]);

  useEffect(() => {
    if (analysis && !loadedAnalysis) {
      setAnalysisSource("fresh-upload");
    }
  }, [analysis, loadedAnalysis]);

  // --- One-time cleanup: delete orphan history entries without stored result ---
  useEffect(() => {
    if (user?.id) {
      cleanupOrphanHistory()
        .then(() => {
          queryClient.invalidateQueries({
            queryKey: ["portfolio-history", user?.id],
          });
        })
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      // Clear any previously loaded history analysis + cached state
      setLoadedAnalysis(null);
      setAnalysisSource(null);
      sessionStorage.removeItem("optionstaxhub-analysis");
      analyzePortfolio(
        {
          file,
          filingStatus: taxProfile?.filing_status || "single",
          estimatedIncome: taxProfile?.estimated_annual_income || 75000,
          taxYear: taxProfile?.tax_year || 2025,
        },
        {
          onSuccess: () => {
            // Refresh history sidebar after successful analysis
            queryClient.invalidateQueries({
              queryKey: ["portfolio-history", user?.id],
            });
          },
        },
      );
      // Reset file input so the same file can be re-uploaded
      e.target.value = "";
    }
  };

  const handleSignOut = async () => {
    sessionStorage.removeItem("optionstaxhub-analysis");
    // Remove cached portfolio history when the user signs out to avoid
    // briefly showing a previous user's history while refetching.
    queryClient.removeQueries({
      queryKey: ["portfolio-history", user?.id],
    });
    queryClient.removeQueries({
      queryKey: ["tax-profile", user?.id],
    });
    await signOut();
    setMenuAnchor(null);
    router.push("/auth/signin");
  };

  /**
   * Load a past analysis from history and display it.
   *
   * Closes the drawer immediately for responsiveness, then fetches the
   * full result from the database in the background. A Snackbar confirms
   * the load (or reports an error).
   */
  const handleHistoryItemClick = async (itemId: string, filename: string) => {
    if (!user?.id) return;
    // Close drawer immediately so the user sees the dashboard right away
    setHistoryOpen(false);
    setHistoryLoading(true);
    try {
      const record = await fetchAnalysisById(itemId);
      if (record?.result) {
        setLoadedAnalysis(normalizeAnalysis(record.result));
        setAnalysisSource("saved-history");
        setActiveTab(0);
        setSnackbar({
          message: `Loaded saved analysis: ${filename}`,
          severity: "success",
        });
      } else {
        setSnackbar({
          message:
            "No detailed data stored for this analysis. Re-upload the original CSV to see full results.",
          severity: "info",
        });
      }
    } catch (err) {
      console.error("Failed to load analysis:", err);
      setSnackbar({
        message: "Failed to load saved analysis. Please try again.",
        severity: "error",
      });
    } finally {
      setHistoryLoading(false);
    }
  };

  /**
   * Delete a history item after user confirms via dialog.
   */
  const handleDeleteConfirm = async () => {
    await deleteHistoryEntry({
      userId: user?.id,
      deleteTarget,
      invalidateHistory: () => {
        queryClient.invalidateQueries({
          queryKey: ["portfolio-history", user?.id],
        });
      },
      clearDeleteTarget: () => setDeleteTarget(null),
    });
  };

  useEffect(() => {
    if (!authLoading && !user) {
      router.push("/auth/signin");
    }
  }, [authLoading, user, router]);

  if (authLoading || !user) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  const firstName = user.user_metadata?.first_name as string | null;
  const lastName = user.user_metadata?.last_name as string | null;
  const fullName = [firstName, lastName].filter(Boolean).join(" ");
  const displayNameFromProfile =
    (user.user_metadata?.display_name as string | null) ||
    (user.user_metadata?.full_name as string | null);
  const displayName =
    displayNameFromProfile || fullName || user.email || "Account";
  const avatarLetter = displayName[0].toUpperCase();

  // displayedAnalysis is computed above (near sessionStorage effects)
  const hasResults = !!displayedAnalysis;
  const confidenceSummary = displayedAnalysis
    ? getConfidenceSummary(displayedAnalysis)
    : null;
  const recommendedNextSteps = displayedAnalysis
    ? getRecommendedNextSteps(displayedAnalysis)
    : [];
  const sourceDisplay = analysisSource
    ? getAnalysisSourceDisplay(analysisSource)
    : null;

  // Pre-compute error message using if/else to avoid nested ternary (SonarQube S3358)
  const analysisErrorMessage = getAnalysisErrorMessage(error);

  return (
    <>
      <ServiceWorkerRegistration />

      {/* Header AppBar */}
      <AppBar position="static">
        <Toolbar sx={{ px: { xs: 1, sm: 2 }, gap: { xs: 0.25, sm: 0.5 } }}>
          <DashboardIcon sx={{ mr: 0.5 }} />
          <Typography
            variant="h6"
            component="div"
            sx={{
              flexGrow: 1,
              fontWeight: 700,
              fontSize: { xs: "1rem", sm: "1.25rem" },
              whiteSpace: "nowrap",
            }}
          >
            OptionsTaxHub
          </Typography>

          {/* Tip — icon-only on mobile */}
          <IconButton
            color="inherit"
            onClick={() => setTipJarOpen(true)}
            aria-label="Tip"
            sx={{ display: { xs: "inline-flex", sm: "none" } }}
          >
            <HeartIcon sx={{ color: "#ff6b6b" }} />
          </IconButton>
          <Button
            color="inherit"
            startIcon={<HeartIcon sx={{ color: "#ff6b6b" }} />}
            onClick={() => setTipJarOpen(true)}
            sx={{
              textTransform: "none",
              mr: 0.5,
              display: { xs: "none", sm: "inline-flex" },
            }}
          >
            Tip
          </Button>

          {/* History — icon-only on mobile */}
          <IconButton
            color="inherit"
            onClick={() => setHistoryOpen(true)}
            aria-label="History"
            sx={{ display: { xs: "inline-flex", sm: "none" } }}
          >
            <HistoryIcon />
          </IconButton>
          <Button
            color="inherit"
            startIcon={<HistoryIcon />}
            onClick={() => setHistoryOpen(true)}
            sx={{
              textTransform: "none",
              mr: 0.5,
              display: { xs: "none", sm: "inline-flex" },
            }}
          >
            History
          </Button>

          {/* Settings — icon-only on mobile */}
          <IconButton
            color="inherit"
            onClick={() => router.push("/settings")}
            aria-label="Settings"
            sx={{ display: { xs: "inline-flex", sm: "none" } }}
          >
            <SettingsIcon />
          </IconButton>
          <Button
            color="inherit"
            startIcon={<SettingsIcon />}
            onClick={() => router.push("/settings")}
            sx={{
              textTransform: "none",
              mr: 0.5,
              display: { xs: "none", sm: "inline-flex" },
            }}
          >
            Settings
          </Button>

          {/* Avatar — name hidden on mobile */}
          <Button
            color="inherit"
            onClick={(e) => setMenuAnchor(e.currentTarget)}
            sx={{
              textTransform: "none",
              minWidth: "auto",
              px: { xs: 0.5, sm: 1 },
            }}
          >
            <Avatar sx={{ width: 32, height: 32 }}>{avatarLetter}</Avatar>
            <Typography
              component="span"
              sx={{ ml: 1, display: { xs: "none", sm: "inline" } }}
            >
              {displayName}
            </Typography>
          </Button>
          <Menu
            anchorEl={menuAnchor}
            open={Boolean(menuAnchor)}
            onClose={() => setMenuAnchor(null)}
          >
            <MenuItem disabled>
              <Typography variant="body2">
                {displayName}
                {user.email ? ` (${user.email})` : ""}
              </Typography>
            </MenuItem>
            <MenuItem onClick={handleSignOut}>
              <LogoutIcon sx={{ mr: 1 }} />
              Sign Out
            </MenuItem>
          </Menu>
        </Toolbar>
      </AppBar>

      {/* Tip Jar Dialog */}
      <TipJar open={tipJarOpen} onClose={() => setTipJarOpen(false)} />

      {/* History Drawer */}
      <Drawer
        anchor="left"
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      >
        <Box sx={{ width: 300, pt: 2 }}>
          <Typography
            variant="h6"
            sx={{
              px: 2,
              pb: 1,
              fontWeight: 700,
              display: "flex",
              alignItems: "center",
              gap: 1,
            }}
          >
            <HistoryIcon /> Upload History
          </Typography>
          <Divider />
          <HistoryContent
            history={history}
            historyError={historyError || null}
            historyLoading={historyLoading}
            onItemClick={handleHistoryItemClick}
            onDeleteClick={(id, filename) => setDeleteTarget({ id, filename })}
          />
        </Box>
      </Drawer>

      {/* Delete Confirmation Dialog */}
      <Dialog open={!!deleteTarget} onClose={() => setDeleteTarget(null)}>
        <DialogTitle>Delete Analysis</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to delete the analysis for{" "}
            <strong>{deleteTarget?.filename}</strong>? This action cannot be
            undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
          <Button
            onClick={handleDeleteConfirm}
            color="error"
            variant="contained"
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* Loading bar — shows for new analysis (isPending) and history fetch (historyLoading) */}
      {isPending && <LinearProgress />}
      {historyLoading && <LinearProgress color="secondary" />}

      {/* Snackbar for history load feedback */}
      <Snackbar
        open={!!snackbar}
        autoHideDuration={4000}
        onClose={() => setSnackbar(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          severity={snackbar?.severity ?? "info"}
          onClose={() => setSnackbar(null)}
          variant="filled"
          sx={{ width: "100%" }}
        >
          {snackbar?.message}
        </Alert>
      </Snackbar>

      {/* Main Content */}
      <Container maxWidth="lg" sx={{ py: 3 }}>
        <Stack spacing={3}>
          {/* Upload Section */}
          <Card>
            <CardContent>
              <Stack spacing={2}>
                <Box>
                  <Typography variant="h5" sx={{ mb: 0.5, fontWeight: 600 }}>
                    Portfolio Analysis
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Upload your Robinhood CSV export to analyze tax-loss
                    harvesting opportunities. Your{" "}
                    <Button
                      size="small"
                      onClick={() => router.push("/settings")}
                      sx={{ textTransform: "none", p: 0, minWidth: "auto" }}
                    >
                      tax profile
                    </Button>{" "}
                    is used to calculate savings.
                  </Typography>
                </Box>

                {/* Upload area */}
                <Box
                  sx={{
                    border: "2px dashed",
                    borderColor: isPending ? "grey.400" : "primary.main",
                    borderRadius: 2,
                    p: 3,
                    textAlign: "center",
                    cursor: isPending ? "default" : "pointer",
                    transition: "all 0.2s",
                    opacity: isPending ? 0.6 : 1,
                    "& *": {
                      cursor: "inherit",
                    },
                    "&:hover": isPending
                      ? {}
                      : {
                          backgroundColor: "action.hover",
                          borderColor: "primary.dark",
                        },
                  }}
                  onClick={isPending ? undefined : handleUploadClick}
                  role={isPending ? undefined : "button"}
                  tabIndex={isPending ? undefined : 0}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".csv"
                    onChange={handleFileChange}
                    style={{ display: "none" }}
                  />
                  <CloudUploadIcon
                    sx={{ fontSize: 40, color: "primary.main", mb: 0.5 }}
                  />
                  <Typography variant="body1" sx={{ fontWeight: 500 }}>
                    {isPending
                      ? "Analyzing portfolio..."
                      : "Click to upload CSV"}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Robinhood transaction export or simplified portfolio CSV
                  </Typography>
                </Box>
              </Stack>
            </CardContent>
          </Card>

          {/* Backend health banner — shown when the API server is unreachable */}
          {backendChecked && backendDown && (
            <Alert severity="warning">
              <AlertTitle>Backend server unreachable</AlertTitle>
              The API server is not responding on port 8011. Run{" "}
              <code>npm run dev:server</code> from the project root, or use the{" "}
              <strong>Server: API</strong> task in VS Code.
            </Alert>
          )}

          {/* Error Alert */}
          {error && (
            <Alert severity="error" icon={<ErrorIcon />}>
              <AlertTitle>Analysis Failed</AlertTitle>{" "}
              <Typography variant="caption">{analysisErrorMessage}</Typography>
            </Alert>
          )}

          {/* Results — shown ABOVE warnings so actionable info is immediately visible */}
          {hasResults && (
            <>
              {/* Tax year chip + summary cards */}
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  flexWrap: "wrap",
                }}
              >
                {!!displayedAnalysis.tax_profile?.tax_year && (
                  <Chip
                    icon={<CalendarIcon />}
                    label={`Tax Year: ${displayedAnalysis.tax_profile.tax_year}`}
                    size="small"
                    color="primary"
                    variant="outlined"
                  />
                )}
                {sourceDisplay && (
                  <Chip
                    label={sourceDisplay.label}
                    size="small"
                    color={sourceDisplay.color}
                    variant="outlined"
                  />
                )}
                {confidenceSummary && (
                  <Chip
                    label={confidenceSummary.label}
                    size="small"
                    color={confidenceSummary.severity}
                    variant="filled"
                  />
                )}
              </Box>

              {confidenceSummary && (
                <Alert severity={confidenceSummary.severity} variant="outlined">
                  <AlertTitle>{confidenceSummary.label}</AlertTitle>
                  <Typography
                    variant="body2"
                    sx={{ mb: confidenceSummary.details.length ? 0.5 : 0 }}
                  >
                    {confidenceSummary.summary}
                  </Typography>
                  {analysisSource === "restored-session" && (
                    <Typography
                      variant="caption"
                      sx={{ display: "block", mb: 0.5 }}
                    >
                      This result was restored from browser storage and may not
                      reflect your newest upload. Re-upload the CSV for a fully
                      fresh run.
                    </Typography>
                  )}
                  {analysisSource === "saved-history" && (
                    <Typography
                      variant="caption"
                      sx={{ display: "block", mb: 0.5 }}
                    >
                      This result was loaded from saved history. Re-upload the
                      CSV if you want the latest prices and a new live analysis.
                    </Typography>
                  )}
                  {confidenceSummary.details.length > 0 && (
                    <Box component="ul" sx={{ pl: 2, my: 0 }}>
                      {confidenceSummary.details.map((detail) => (
                        <Typography
                          component="li"
                          variant="caption"
                          key={detail}
                        >
                          {detail}
                        </Typography>
                      ))}
                    </Box>
                  )}
                </Alert>
              )}

              {recommendedNextSteps.length > 0 && (
                <Alert severity="info" variant="outlined">
                  <AlertTitle>Recommended next steps</AlertTitle>
                  <Box component="ol" sx={{ pl: 2.5, my: 0 }}>
                    {recommendedNextSteps.map((step) => (
                      <Typography component="li" variant="body2" key={step}>
                        {step}
                      </Typography>
                    ))}
                  </Box>
                </Alert>
              )}

              <PortfolioSummaryCards summary={displayedAnalysis.summary} />

              {/* Wash-Sale Warnings — grouped by ticker accordion */}
              {displayedAnalysis.wash_sale_flags.length > 0 && (
                <WashSaleWarning
                  flags={displayedAnalysis.wash_sale_flags}
                  taxYear={displayedAnalysis.tax_profile?.tax_year}
                />
              )}

              {/* Tabbed view: Suggestions first (most actionable), then Positions */}
              <Card>
                <Tabs
                  value={activeTab}
                  onChange={(_, v) => setActiveTab(v)}
                  sx={{ borderBottom: 1, borderColor: "divider", px: 2 }}
                >
                  <Tab
                    label={`Suggestions (${displayedAnalysis.suggestions.length})`}
                    id="tab-suggestions"
                  />
                  <Tab
                    label={`Positions (${displayedAnalysis.positions.length})`}
                    id="tab-positions"
                  />
                </Tabs>

                <CardContent>
                  {activeTab === 0 && (
                    <SuggestionsPanel
                      suggestions={displayedAnalysis.suggestions}
                      skippedSuggestionSymbols={skippedSuggestionSymbols}
                    />
                  )}
                  {activeTab === 1 && (
                    <PositionsTable positions={displayedAnalysis.positions} />
                  )}
                </CardContent>
              </Card>

              {/* Disclaimer */}
              <TaxDisclaimer />
            </>
          )}

          {/* Data quality notes — collapsible accordion, shown below results */}
          {displayedAnalysis?.warnings &&
            displayedAnalysis.warnings.length > 0 && (
              <Accordion
                elevation={0}
                sx={{
                  border: "1px solid",
                  borderColor: "warning.light",
                  borderRadius: 1,
                  "&:before": { display: "none" },
                }}
              >
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography
                    variant="body2"
                    color="warning.dark"
                    sx={{ fontWeight: 500 }}
                  >
                    ⚠ {displayedAnalysis.warnings.length} data quality note
                    {displayedAnalysis.warnings.length === 1 ? "" : "s"} — click
                    to expand
                  </Typography>
                </AccordionSummary>
                <AccordionDetails sx={{ pt: 0 }}>
                  <Alert
                    severity="warning"
                    variant="outlined"
                    sx={{ border: 0 }}
                  >
                    {Array.from(
                      new Set<string>(displayedAnalysis.warnings),
                    ).map((w: string) => (
                      <Typography
                        key={w}
                        variant="caption"
                        sx={{ display: "block", mb: 0.25 }}
                      >
                        {w}
                      </Typography>
                    ))}
                  </Alert>
                </AccordionDetails>
              </Accordion>
            )}
        </Stack>
      </Container>
    </>
  );
}
