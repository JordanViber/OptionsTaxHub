"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AppBar,
  Toolbar,
  Container,
  Box,
  Button,
  Stack,
  Typography,
  Card,
  CardContent,
  Grid,
  Chip,
} from "@mui/material";
import {
  Dashboard as DashboardIcon,
  TrendingDown as HarvestIcon,
  Warning as WashSaleIcon,
  CloudUpload as UploadIcon,
  Security as SecurityIcon,
  Speed as SpeedIcon,
  AccountBalanceWallet as WalletIcon,
} from "@mui/icons-material";
import { useAuth } from "@/app/context/auth";

export default function LandingPage() {
  const router = useRouter();
  const { user, loading } = useAuth();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // If already authenticated, redirect to dashboard
  useEffect(() => {
    if (!loading && user) {
      router.push("/dashboard");
    }
  }, [loading, user, router]);

  // Prevent hydration mismatch
  if (!mounted) return null;

  // Show nothing while checking auth (brief flash prevention)
  if (loading) return null;

  // If user is authenticated, show nothing while redirecting
  if (user) return null;

  const features = [
    {
      icon: <UploadIcon sx={{ fontSize: 40, color: "primary.main" }} />,
      title: "CSV Upload & Analysis",
      description:
        "Upload your Robinhood transaction exports and get instant portfolio analysis with detailed position breakdowns.",
    },
    {
      icon: <HarvestIcon sx={{ fontSize: 40, color: "success.main" }} />,
      title: "Tax-Loss Harvesting",
      description:
        "Identify positions with unrealized losses and get smart suggestions to harvest tax savings before year-end.",
    },
    {
      icon: <WashSaleIcon sx={{ fontSize: 40, color: "warning.main" }} />,
      title: "Wash-Sale Detection",
      description:
        "Automatically flag potential wash-sale rule violations so you can avoid costly IRS penalties.",
    },
    {
      icon: <WalletIcon sx={{ fontSize: 40, color: "info.main" }} />,
      title: "Tax Savings Estimates",
      description:
        "See estimated federal and state tax savings based on your filing status, income bracket, and tax year.",
    },
    {
      icon: <SpeedIcon sx={{ fontSize: 40, color: "secondary.main" }} />,
      title: "Instant Results",
      description:
        "Get portfolio analysis in seconds. No waiting, no complex setup — just upload and go.",
    },
    {
      icon: <SecurityIcon sx={{ fontSize: 40, color: "error.main" }} />,
      title: "Privacy First",
      description:
        "Your data is processed securely and never stored permanently. CSVs are analyzed in-memory only.",
    },
  ];

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      {/* Navigation */}
      <AppBar position="static" elevation={0}>
        <Toolbar sx={{ px: { xs: 2, sm: 3 } }}>
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
            onClick={() => router.push("/auth/signin")}
            sx={{ textTransform: "none", mr: 1 }}
          >
            Sign In
          </Button>
          <Button
            variant="contained"
            color="secondary"
            onClick={() => router.push("/auth/signup")}
            sx={{ textTransform: "none", fontWeight: 600 }}
          >
            Get Started
          </Button>
        </Toolbar>
      </AppBar>

      {/* Hero Section */}
      <Box
        sx={{
          background: "linear-gradient(135deg, #1565c0 0%, #0d47a1 100%)",
          color: "white",
          py: { xs: 8, md: 12 },
          px: 2,
          textAlign: "center",
        }}
      >
        <Container maxWidth="md">
          <Chip
            label="Free to use — No credit card required"
            sx={{
              mb: 3,
              bgcolor: "rgba(255,255,255,0.15)",
              color: "white",
              fontWeight: 600,
              fontSize: "0.85rem",
            }}
          />
          <Typography
            variant="h2"
            sx={{
              fontWeight: 800,
              mb: 2,
              fontSize: { xs: "2rem", sm: "2.75rem", md: "3.5rem" },
              lineHeight: 1.2,
            }}
          >
            Smart Tax Optimization
            <br />
            for Options Traders
          </Typography>
          <Typography
            variant="h6"
            sx={{
              mb: 4,
              opacity: 0.9,
              maxWidth: 600,
              mx: "auto",
              fontWeight: 400,
              fontSize: { xs: "1rem", sm: "1.15rem" },
              lineHeight: 1.6,
            }}
          >
            Upload your portfolio, identify tax-loss harvesting opportunities,
            detect wash-sale violations, and estimate your savings — all in
            seconds.
          </Typography>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={2}
            justifyContent="center"
          >
            <Button
              variant="contained"
              size="large"
              onClick={() => router.push("/auth/signup")}
              sx={{
                py: 1.5,
                px: 4,
                fontSize: "1.1rem",
                fontWeight: 700,
                textTransform: "none",
                bgcolor: "white",
                color: "primary.dark",
                "&:hover": { bgcolor: "grey.100" },
              }}
            >
              Create Free Account
            </Button>
            <Button
              variant="outlined"
              size="large"
              onClick={() => router.push("/auth/signin")}
              sx={{
                py: 1.5,
                px: 4,
                fontSize: "1.1rem",
                fontWeight: 700,
                textTransform: "none",
                borderColor: "rgba(255,255,255,0.5)",
                color: "white",
                "&:hover": {
                  borderColor: "white",
                  bgcolor: "rgba(255,255,255,0.1)",
                },
              }}
            >
              Sign In
            </Button>
          </Stack>
        </Container>
      </Box>

      {/* Features Grid */}
      <Container maxWidth="lg" sx={{ py: { xs: 6, md: 10 } }}>
        <Typography
          variant="h4"
          sx={{
            textAlign: "center",
            fontWeight: 700,
            mb: 1,
            fontSize: { xs: "1.5rem", md: "2rem" },
          }}
        >
          Everything You Need for Tax-Smart Trading
        </Typography>
        <Typography
          variant="body1"
          color="text.secondary"
          sx={{ textAlign: "center", mb: 6, maxWidth: 600, mx: "auto" }}
        >
          Built for DIY retail investors who want to maximize after-tax returns
          without the complexity.
        </Typography>
        <Grid container spacing={3}>
          {features.map((feature) => (
            <Grid size={{ xs: 12, sm: 6, md: 4 }} key={feature.title}>
              <Card
                sx={{
                  height: "100%",
                  transition: "transform 0.2s, box-shadow 0.2s",
                  "&:hover": {
                    transform: "translateY(-4px)",
                    boxShadow: 6,
                  },
                }}
              >
                <CardContent sx={{ p: 3 }}>
                  <Box sx={{ mb: 2 }}>{feature.icon}</Box>
                  <Typography
                    variant="h6"
                    sx={{ fontWeight: 700, mb: 1, fontSize: "1.1rem" }}
                  >
                    {feature.title}
                  </Typography>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ lineHeight: 1.6 }}
                  >
                    {feature.description}
                  </Typography>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      </Container>

      {/* How It Works */}
      <Box sx={{ bgcolor: "grey.50", py: { xs: 6, md: 10 }, px: 2 }}>
        <Container maxWidth="md">
          <Typography
            variant="h4"
            sx={{
              textAlign: "center",
              fontWeight: 700,
              mb: 6,
              fontSize: { xs: "1.5rem", md: "2rem" },
            }}
          >
            How It Works
          </Typography>
          <Stack spacing={4}>
            {[
              {
                step: "1",
                title: "Create your free account",
                desc: "Sign up in seconds with just an email and password.",
              },
              {
                step: "2",
                title: "Upload your CSV export",
                desc: "Export your transaction history from Robinhood (or use our simplified format) and upload the CSV.",
              },
              {
                step: "3",
                title: "Get instant tax insights",
                desc: "See your portfolio breakdown, harvesting suggestions, wash-sale warnings, and estimated tax savings immediately.",
              },
            ].map((item) => (
              <Stack
                key={item.step}
                direction="row"
                spacing={3}
                alignItems="flex-start"
              >
                <Box
                  sx={{
                    width: 48,
                    height: 48,
                    borderRadius: "50%",
                    bgcolor: "primary.main",
                    color: "white",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontWeight: 800,
                    fontSize: "1.25rem",
                    flexShrink: 0,
                  }}
                >
                  {item.step}
                </Box>
                <Box>
                  <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>
                    {item.title}
                  </Typography>
                  <Typography variant="body1" color="text.secondary">
                    {item.desc}
                  </Typography>
                </Box>
              </Stack>
            ))}
          </Stack>
        </Container>
      </Box>

      {/* CTA Section */}
      <Box
        sx={{
          py: { xs: 6, md: 10 },
          px: 2,
          textAlign: "center",
        }}
      >
        <Container maxWidth="sm">
          <Typography
            variant="h4"
            sx={{
              fontWeight: 700,
              mb: 2,
              fontSize: { xs: "1.5rem", md: "2rem" },
            }}
          >
            Ready to Optimize Your Taxes?
          </Typography>
          <Typography
            variant="body1"
            color="text.secondary"
            sx={{ mb: 4, lineHeight: 1.6 }}
          >
            Join OptionsTaxHub today and start identifying tax-loss harvesting
            opportunities in your portfolio.
          </Typography>
          <Button
            variant="contained"
            size="large"
            onClick={() => router.push("/auth/signup")}
            sx={{
              py: 1.5,
              px: 5,
              fontSize: "1.1rem",
              fontWeight: 700,
              textTransform: "none",
            }}
          >
            Get Started — It&apos;s Free
          </Button>
        </Container>
      </Box>

      {/* Footer */}
      <Box
        sx={{
          bgcolor: "grey.900",
          color: "grey.400",
          py: 4,
          px: 2,
          textAlign: "center",
        }}
      >
        <Container maxWidth="md">
          <Typography variant="body2" sx={{ mb: 1 }}>
            &copy; {new Date().getFullYear()} OptionsTaxHub. All rights
            reserved.
          </Typography>
          <Typography
            variant="caption"
            sx={{ display: "block", maxWidth: 500, mx: "auto", lineHeight: 1.5 }}
          >
            This tool is for educational and informational purposes only. It
            does not constitute financial, tax, or legal advice. Consult a
            qualified professional before making tax decisions.
          </Typography>
        </Container>
      </Box>
    </Box>
  );
}
