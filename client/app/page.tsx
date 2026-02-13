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
  Stack,
  Typography,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Menu,
  MenuItem,
  Avatar,
} from "@mui/material";
import {
  CloudUpload as CloudUploadIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Logout as LogoutIcon,
} from "@mui/icons-material";
import InstallPrompt from "./components/InstallPrompt";
import ServiceWorkerRegistration from "./components/ServiceWorkerRegistration";
import { useUploadPortfolio } from "@/lib/api";
import { useAuth } from "@/app/context/auth";

export const dynamic = "force-dynamic";

export default function Home() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { user, loading: authLoading, signOut } = useAuth();
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);

  const {
    mutate: uploadPortfolio,
    isPending,
    error,
    data: portfolioData,
  } = useUploadPortfolio();

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      uploadPortfolio(file);
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

  // Redirect to sign in if not authenticated
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

  // Format portfolio data for table display
  const displayData = portfolioData || [];
  const columns = displayData.length > 0 ? Object.keys(displayData[0]) : [];

  return (
    <>
      <ServiceWorkerRegistration />
      <InstallPrompt />

      {/* Header AppBar */}
      <AppBar position="static" sx={{ mb: 4 }}>
        <Toolbar>
          <Typography
            variant="h6"
            component="div"
            sx={{ flexGrow: 1, fontWeight: 700 }}
          >
            OptionsTaxHub
          </Typography>
          <Typography variant="body2" sx={{ opacity: 0.9, mr: 2 }}>
            Tax-Optimized Options Trading
          </Typography>
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

      {/* Main Content */}
      <Container maxWidth="lg" sx={{ py: 4 }}>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {/* Upload Section */}
          <Card>
            <CardContent>
              <Stack spacing={3}>
                <Box>
                  <Typography variant="h5" sx={{ mb: 1, fontWeight: 600 }}>
                    Upload Portfolio
                  </Typography>
                  <Typography variant="body2" color="textSecondary">
                    Upload your portfolio CSV file to analyze tax-loss
                    harvesting opportunities
                  </Typography>
                </Box>

                {/* File Input Area */}
                <Box
                  sx={{
                    border: "2px dashed",
                    borderColor: "primary.main",
                    borderRadius: 2,
                    p: 3,
                    textAlign: "center",
                    cursor: "pointer",
                    transition: "all 0.2s",
                    "&:hover": {
                      backgroundColor: "action.hover",
                      borderColor: "primary.dark",
                    },
                  }}
                  onClick={handleUploadClick}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".csv"
                    onChange={handleFileChange}
                    style={{ display: "none" }}
                  />
                  <CloudUploadIcon
                    sx={{
                      fontSize: 48,
                      color: "primary.main",
                      mb: 1,
                    }}
                  />
                  <Typography variant="body1" sx={{ fontWeight: 500 }}>
                    Click to upload or drag and drop
                  </Typography>
                  <Typography variant="caption" color="textSecondary">
                    CSV format only
                  </Typography>
                </Box>

                {/* Upload Button */}
                <Button
                  variant="contained"
                  size="large"
                  onClick={handleUploadClick}
                  disabled={isPending}
                  endIcon={
                    isPending ? (
                      <CircularProgress size={20} />
                    ) : (
                      <CloudUploadIcon />
                    )
                  }
                  sx={{ py: 1.5 }}
                >
                  {isPending ? "Uploading..." : "Upload CSV"}
                </Button>
              </Stack>
            </CardContent>
          </Card>

          {/* Error Alert */}
          {error && (
            <Alert
              severity="error"
              icon={<ErrorIcon />}
              sx={{ mb: 2 }}
              onClose={() => {
                // Error will persist in React Query cache, but can be dismissed from UI
              }}
            >
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                Upload Failed
              </Typography>
              <Typography variant="caption">
                {error instanceof Error ? error.message : "An error occurred"}
              </Typography>
            </Alert>
          )}

          {/* Results Section */}
          {displayData.length > 0 && (
            <Card>
              <CardContent>
                <Box
                  sx={{ mb: 3, display: "flex", alignItems: "center", gap: 1 }}
                >
                  <CheckCircleIcon sx={{ color: "success.main" }} />
                  <Typography variant="h5" sx={{ fontWeight: 600 }}>
                    Portfolio Data (First 5 Rows)
                  </Typography>
                </Box>

                <TableContainer component={Paper} variant="outlined">
                  <Table>
                    <TableHead>
                      <TableRow sx={{ backgroundColor: "#f5f5f5" }}>
                        {columns.map((col) => (
                          <TableCell
                            key={col}
                            sx={{
                              fontWeight: 700,
                              color: "primary.main",
                              fontSize: "0.875rem",
                            }}
                          >
                            {col}
                          </TableCell>
                        ))}
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {displayData.map((row) => {
                        // Use a combination of values to create a unique key
                        const rowKey = Object.values(row).join("-");
                        return (
                          <TableRow
                            key={rowKey}
                            sx={{
                              "&:hover": { backgroundColor: "action.hover" },
                              "&:last-child td": { borderBottom: 0 },
                            }}
                          >
                            {columns.map((col) => (
                              <TableCell
                                key={col}
                                sx={{ fontSize: "0.875rem" }}
                              >
                                {typeof row[col] === "number"
                                  ? Number.parseFloat(String(row[col])).toFixed(
                                      2,
                                    )
                                  : String(row[col])}
                              </TableCell>
                            ))}
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </TableContainer>

                <Typography
                  variant="caption"
                  color="textSecondary"
                  sx={{ mt: 2, display: "block" }}
                >
                  Showing portfolio summary - full analysis features coming soon
                </Typography>
              </CardContent>
            </Card>
          )}
        </Box>
      </Container>
    </>
  );
}
