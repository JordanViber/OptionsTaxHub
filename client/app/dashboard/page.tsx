"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AppBar,
  Toolbar,
  Container,
  Box,
  Card,
  CardContent,
  Button,
  CircularProgress,
  Alert,
  AlertTitle,
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
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
  Logout as LogoutIcon,
  Settings as SettingsIcon,
  Dashboard as DashboardIcon,
  History as HistoryIcon,
  Favorite as HeartIcon,
  Close as CloseIcon,
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
  fetchAnalysisById,
  cleanupOrphanHistory,
  deleteAnalysis,
} from "@/lib/api";
import { useAuth } from "@/app/context/auth";
import { useQueryClient } from "@tanstack/react-query";
import type { PortfolioAnalysis } from "@/lib/types";
export const dynamic = "force-dynamic";

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
  const [historyLoading, setHistoryLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    id: string;
    filename: string;
  } | null>(null);
  const queryClient = useQueryClient();

  // Load the user's tax profile for analyze params
  const { data: taxProfile } = useTaxProfile();

  // Load past upload history
  const {
    data: history,
    error: historyError,
    isPending: isHistoryFetching,
  } = usePortfolioHistory();

  // Full portfolio analysis mutation
  const {
    mutate: analyzePortfolio,
    isPending,
    error,
    data: analysis,
  } = useAnalyzePortfolio();

  // --- State persistence: restore analysis from sessionStorage on mount ---
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem("optionstaxhub-analysis");
      if (saved) {
        setLoadedAnalysis(JSON.parse(saved) as PortfolioAnalysis);
      }
    } catch {
      // Corrupted data — ignore
    }
  }, []);

  // --- State persistence: save displayedAnalysis whenever it changes ---
  const displayedAnalysis = loadedAnalysis || analysis;
  useEffect(() => {
    if (displayedAnalysis) {
      try {
        sessionStorage.setItem(
          "optionstaxhub-analysis",
          JSON.stringify(displayedAnalysis),
        );
      } catch {
        // Storage full — ignore
      }
    }
  }, [displayedAnalysis]);

  // --- One-time cleanup: delete orphan history entries without stored result ---
  useEffect(() => {
    if (user?.id) {
      cleanupOrphanHistory()
        .then(() => {
          queryClient.invalidateQueries({
            queryKey: ["portfolio-history"],
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
              queryKey: ["portfolio-history"],
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
      queryKey: ["portfolio-history"],
    });
    await signOut();
    setMenuAnchor(null);
    router.push("/auth/signin");
  };

  /**
   * Load a past analysis from history and display it.
   */
  const handleHistoryItemClick = async (itemId: string) => {
    if (!user?.id) return;
    setHistoryLoading(true);
    try {
      const record = await fetchAnalysisById(itemId);
      if (record?.result) {
        setLoadedAnalysis(record.result);
        setActiveTab(0);
      }
    } catch (err) {
      // Silently fail — old items may not have full result stored
      console.error("Failed to load analysis:", err);
    } finally {
      setHistoryLoading(false);
      setHistoryOpen(false);
    }
  };

  /**
   * Delete a history item after user confirms via dialog.
   */
  const handleDeleteConfirm = async () => {
    if (!user?.id || !deleteTarget) return;
    try {
      await deleteAnalysis(deleteTarget.id);
      queryClient.invalidateQueries({
        queryKey: ["portfolio-history"],
      });
    } catch (err) {
      console.error("Failed to delete analysis:", err);
    } finally {
      setDeleteTarget(null);
    }
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
          {historyError ? (
            <Alert severity="error" sx={{ m: 2 }}>
              <AlertTitle>Failed to Load History</AlertTitle>
              {historyError instanceof Error
                ? historyError.message
                : "Unable to load your past uploads. Please try refreshing the page."}
            </Alert>
          ) : !history || history.length === 0 ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ p: 2, textAlign: "center" }}
            >
              No past uploads yet. Upload a CSV to get started.
            </Typography>
          ) : (
            <List
              dense
              sx={{ overflow: "auto", maxHeight: "calc(100vh - 80px)" }}
            >
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
                        setDeleteTarget({
                          id: item.id,
                          filename: item.filename,
                        });
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
                    onClick={() => handleHistoryItemClick(item.id)}
                    disabled={historyLoading}
                    sx={{ pr: 5 }}
                  >
                    <ListItemText
                      primary={item.filename}
                      secondary={
                        <>
                          {new Date(item.uploaded_at).toLocaleDateString(
                            "en-US",
                            {
                              month: "short",
                              day: "numeric",
                              year: "numeric",
                              hour: "numeric",
                              minute: "2-digit",
                            },
                          )}
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
          )}
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

      {/* Loading bar */}
      {(isPending || historyLoading) && <LinearProgress />}

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

          {/* Error Alert */}
          {error && (
            <Alert severity="error" icon={<ErrorIcon />}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                Analysis Failed
              </Typography>
              <Typography variant="caption">
                {error instanceof Error ? error.message : "An error occurred"}
              </Typography>
            </Alert>
          )}

          {/* Warnings */}
          {displayedAnalysis?.warnings &&
            displayedAnalysis.warnings.length > 0 && (
              <Alert severity="warning">
                {displayedAnalysis.warnings.map((w: string) => (
                  <Typography key={w} variant="body2">
                    {w}
                  </Typography>
                ))}
              </Alert>
            )}

          {/* Results */}
          {hasResults && (
            <>
              {/* Summary Cards */}
              <PortfolioSummaryCards summary={displayedAnalysis.summary} />

              {/* Wash-Sale Warnings */}
              {displayedAnalysis.wash_sale_flags.length > 0 && (
                <WashSaleWarning flags={displayedAnalysis.wash_sale_flags} />
              )}

              {/* Tabbed view: Positions | Suggestions */}
              <Card>
                <Tabs
                  value={activeTab}
                  onChange={(_, v) => setActiveTab(v)}
                  sx={{ borderBottom: 1, borderColor: "divider", px: 2 }}
                >
                  <Tab
                    label={`Positions (${displayedAnalysis.positions.length})`}
                    id="tab-positions"
                  />
                  <Tab
                    label={`Suggestions (${displayedAnalysis.suggestions.length})`}
                    id="tab-suggestions"
                  />
                </Tabs>

                <CardContent>
                  {activeTab === 0 && (
                    <PositionsTable positions={displayedAnalysis.positions} />
                  )}
                  {activeTab === 1 && (
                    <HarvestingSuggestions
                      suggestions={displayedAnalysis.suggestions}
                    />
                  )}
                </CardContent>
              </Card>

              {/* Disclaimer */}
              <TaxDisclaimer />
            </>
          )}
        </Stack>
      </Container>
    </>
  );
}
