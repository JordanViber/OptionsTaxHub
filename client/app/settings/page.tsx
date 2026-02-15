"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AppBar,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Container,
  FormControl,
  InputAdornment,
  InputLabel,
  MenuItem,
  Select,
  Snackbar,
  Alert,
  Stack,
  TextField,
  Toolbar,
  Typography,
} from "@mui/material";
import { Save as SaveIcon, ArrowBack as BackIcon } from "@mui/icons-material";
import { useAuth } from "@/app/context/auth";
import { useSaveTaxProfile, useTaxProfile } from "@/lib/api";
import type { FilingStatus } from "@/lib/types";
import { FILING_STATUS_LABELS, US_STATES } from "@/lib/types";
import TaxDisclaimer from "@/app/components/TaxDisclaimer";

export const dynamic = "force-dynamic";

export default function SettingsPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  // Form state
  const [filingStatus, setFilingStatus] = useState<FilingStatus>("single");
  const [estimatedIncome, setEstimatedIncome] = useState<string>("75000");
  const [state, setState] = useState<string>("");
  const [taxYear, setTaxYear] = useState<number>(2025);
  const [showSuccess, setShowSuccess] = useState(false);

  // Load existing profile
  const { data: profile, isLoading: profileLoading } = useTaxProfile(user?.id);

  // Save mutation
  const { mutate: saveProfile, isPending: saving } = useSaveTaxProfile();

  // Populate form when profile loads
  useEffect(() => {
    if (profile) {
      setFilingStatus(profile.filing_status || "single");
      setEstimatedIncome(String(profile.estimated_annual_income || 75000));
      setState(profile.state || "");
      setTaxYear(profile.tax_year || 2025);
    }
  }, [profile]);

  // Redirect if not authenticated
  useEffect(() => {
    if (!authLoading && !user) {
      router.push("/");
    }
  }, [authLoading, user, router]);

  const handleSave = () => {
    saveProfile(
      {
        user_id: user?.id,
        filing_status: filingStatus,
        estimated_annual_income: Number.parseFloat(estimatedIncome) || 75000,
        state,
        tax_year: taxYear,
      },
      {
        onSuccess: () => {
          setShowSuccess(true);
          // Navigate back to dashboard after brief success message
          setTimeout(() => router.push("/dashboard"), 1500);
        },
      },
    );
  };

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

  return (
    <>
      <AppBar position="static" sx={{ mb: 4 }}>
        <Toolbar sx={{ px: { xs: 1, sm: 2 } }}>
          <Button
            color="inherit"
            startIcon={<BackIcon />}
            onClick={() => router.push("/dashboard")}
            sx={{ mr: 2, textTransform: "none" }}
          >
            Dashboard
          </Button>
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
            Tax Profile Settings
          </Typography>
        </Toolbar>
      </AppBar>

      <Container maxWidth="sm" sx={{ py: 2 }}>
        <Stack spacing={3}>
          <TaxDisclaimer />

          <Card>
            <CardContent>
              <Typography variant="h5" sx={{ fontWeight: 600, mb: 3 }}>
                Your Tax Profile
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                These settings help estimate your tax savings from harvesting
                losses. Your tax bracket determines how much each dollar of loss
                saves you.
              </Typography>

              {profileLoading ? (
                <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
                  <CircularProgress />
                </Box>
              ) : (
                <Stack spacing={3}>
                  {/* Filing Status */}
                  <FormControl fullWidth>
                    <InputLabel id="filing-status-label">
                      Filing Status
                    </InputLabel>
                    <Select
                      labelId="filing-status-label"
                      value={filingStatus}
                      label="Filing Status"
                      onChange={(e) =>
                        setFilingStatus(e.target.value as FilingStatus)
                      }
                    >
                      {Object.entries(FILING_STATUS_LABELS).map(
                        ([value, label]) => (
                          <MenuItem key={value} value={value}>
                            {label}
                          </MenuItem>
                        ),
                      )}
                    </Select>
                  </FormControl>

                  {/* Estimated Annual Income */}
                  <TextField
                    label="Estimated Annual Income"
                    type="number"
                    value={estimatedIncome}
                    onChange={(e) => setEstimatedIncome(e.target.value)}
                    slotProps={{
                      input: {
                        startAdornment: (
                          <InputAdornment position="start">$</InputAdornment>
                        ),
                      },
                    }}
                    helperText="Your total estimated income (W-2, 1099, etc.) for the tax year"
                    fullWidth
                  />

                  {/* State */}
                  <FormControl fullWidth>
                    <InputLabel id="state-label">State</InputLabel>
                    <Select
                      labelId="state-label"
                      value={state}
                      label="State"
                      onChange={(e) => setState(e.target.value)}
                    >
                      {US_STATES.map((s) => (
                        <MenuItem key={s.value} value={s.value}>
                          {s.label}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>

                  {/* Tax Year */}
                  <FormControl fullWidth>
                    <InputLabel id="tax-year-label">Tax Year</InputLabel>
                    <Select
                      labelId="tax-year-label"
                      value={taxYear}
                      label="Tax Year"
                      onChange={(e) => setTaxYear(Number(e.target.value))}
                    >
                      <MenuItem value={2025}>2025</MenuItem>
                      <MenuItem value={2026}>2026</MenuItem>
                    </Select>
                  </FormControl>

                  {/* Save Button */}
                  <Button
                    variant="contained"
                    size="large"
                    startIcon={
                      saving ? (
                        <CircularProgress size={20} color="inherit" />
                      ) : (
                        <SaveIcon />
                      )
                    }
                    onClick={handleSave}
                    disabled={saving}
                    sx={{ py: 1.5 }}
                  >
                    {saving ? "Saving..." : "Save Tax Profile"}
                  </Button>
                </Stack>
              )}
            </CardContent>
          </Card>
        </Stack>
      </Container>

      {/* Success Snackbar */}
      <Snackbar
        open={showSuccess}
        autoHideDuration={3000}
        onClose={() => setShowSuccess(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          severity="success"
          onClose={() => setShowSuccess(false)}
          sx={{ width: "100%" }}
        >
          Tax profile saved successfully!
        </Alert>
      </Snackbar>
    </>
  );
}
