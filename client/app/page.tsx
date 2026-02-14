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
} from "@mui/material";
import {
  CloudUpload as CloudUploadIcon,
  Error as ErrorIcon,
  Logout as LogoutIcon,
  Settings as SettingsIcon,
  Dashboard as DashboardIcon,
  History as HistoryIcon,
} from "@mui/icons-material";
import ServiceWorkerRegistration from "./components/ServiceWorkerRegistration";
import TaxDisclaimer from "./components/TaxDisclaimer";
import PortfolioSummaryCards from "./components/PortfolioSummaryCards";
import PositionsTable from "./components/PositionsTable";
import HarvestingSuggestions from "./components/HarvestingSuggestions";
import WashSaleWarning from "./components/WashSaleWarning";
import {
  useAnalyzePortfolio,
  useTaxProfile,
  usePortfolioHistory,
} from "@/lib/api";
import { useAuth } from "@/app/context/auth";
import { useQueryClient } from "@tanstack/react-query";
export const dynamic = "force-dynamic";

export default function Home() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { user, loading: authLoading, signOut } = useAuth();
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);
  const [activeTab, setActiveTab] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const queryClient = useQueryClient();

  // Load the user's tax profile for analyze params
  const { data: taxProfile } = useTaxProfile(user?.id);

  // Load past upload history
  const { data: history } = usePortfolioHistory(user?.id);

  // Full portfolio analysis mutation
  const {
    mutate: analyzePortfolio,
    isPending,
    error,
    data: analysis,
  } = useAnalyzePortfolio();

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      analyzePortfolio(
        {
          file,
          filingStatus: taxProfile?.filing_status || "single",
          estimatedIncome: taxProfile?.estimated_annual_income || 75000,
          taxYear: taxProfile?.tax_year || 2025,
          userId: user?.id,
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
    await signOut();
    setMenuAnchor(null);
    router.push("/auth/signin");
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

  const hasResults = !!analysis;

  return (
    <>
      <ServiceWorkerRegistration />

      {/* Header AppBar */}
      <AppBar position="static">
        <Toolbar>
          <DashboardIcon sx={{ mr: 1 }} />
          <Typography
            variant="h6"
            component="div"
            sx={{ flexGrow: 1, fontWeight: 700 }}
          >
            OptionsTaxHub
          </Typography>
          <Button
            color="inherit"
            startIcon={<HistoryIcon />}
            onClick={() => setHistoryOpen(true)}
            sx={{ textTransform: "none", mr: 1 }}
          >
            History
          </Button>
          <Button
            color="inherit"
            startIcon={<SettingsIcon />}
            onClick={() => router.push("/settings")}
            sx={{ textTransform: "none", mr: 1 }}
          >
            Settings
          </Button>
          <Button
            color="inherit"
            onClick={(e) => setMenuAnchor(e.currentTarget)}
            sx={{ textTransform: "none" }}
          >
            <Avatar sx={{ mr: 1, width: 32, height: 32 }}>
              {avatarLetter}
            </Avatar>
            {displayName}
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
          {!history || history.length === 0 ? (
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
                <ListItem key={item.id} disablePadding>
                  <ListItemButton onClick={() => setHistoryOpen(false)}>
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

      {/* Loading bar */}
      {isPending && <LinearProgress />}

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
          {analysis?.warnings && analysis.warnings.length > 0 && (
            <Alert severity="warning">
              {analysis.warnings.map((w: string) => (
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
              <PortfolioSummaryCards summary={analysis.summary} />

              {/* Wash-Sale Warnings */}
              {analysis.wash_sale_flags.length > 0 && (
                <WashSaleWarning flags={analysis.wash_sale_flags} />
              )}

              {/* Tabbed view: Positions | Suggestions */}
              <Card>
                <Tabs
                  value={activeTab}
                  onChange={(_, v) => setActiveTab(v)}
                  sx={{ borderBottom: 1, borderColor: "divider", px: 2 }}
                >
                  <Tab
                    label={`Positions (${analysis.positions.length})`}
                    id="tab-positions"
                  />
                  <Tab
                    label={`Suggestions (${analysis.suggestions.length})`}
                    id="tab-suggestions"
                  />
                </Tabs>

                <CardContent>
                  {activeTab === 0 && (
                    <PositionsTable positions={analysis.positions} />
                  )}
                  {activeTab === 1 && (
                    <HarvestingSuggestions suggestions={analysis.suggestions} />
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
