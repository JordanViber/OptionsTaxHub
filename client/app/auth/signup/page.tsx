"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Container,
  Box,
  Card,
  CardContent,
  TextField,
  Button,
  Typography,
  Alert,
  Stack,
  Link as MuiLink,
  CircularProgress,
  InputAdornment,
  IconButton,
  MenuItem,
} from "@mui/material";
import { Visibility, VisibilityOff } from "@mui/icons-material";
import { useAuth } from "@/app/context/auth";

export const dynamic = "force-dynamic";

export default function SignUpPage() {
  const router = useRouter();
  const { signUp } = useAuth();
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [providerType, setProviderType] = useState("email");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    if (password.length < 6) {
      setError("Password must be at least 6 characters");
      return;
    }

    if (providerType === "phone" && !phone.trim()) {
      setError("Phone number is required for phone sign-up");
      return;
    }

    if (providerType !== "phone" && !email.trim()) {
      setError("Email is required for email sign-up");
      return;
    }

    setLoading(true);

    try {
      const resolvedDisplayName =
        displayName.trim() || `${firstName} ${lastName}`.trim();
      await signUp(email, password, {
        firstName,
        lastName,
        displayName: resolvedDisplayName,
        phone,
        providerType,
      });
      // Show success message and redirect to sign in
      alert(
        "Sign up successful! Please check your email to confirm your account.",
      );
      router.push("/auth/signin");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to sign up. Please try again.",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container maxWidth="sm" sx={{ py: 8 }}>
      <Card>
        <CardContent>
          <Stack spacing={3}>
            <Box>
              <Typography variant="h4" sx={{ mb: 1, fontWeight: 700 }}>
                Create Account
              </Typography>
              <Typography variant="body2" color="textSecondary">
                Join OptionsTaxHub today
              </Typography>
            </Box>

            {error && <Alert severity="error">{error}</Alert>}

            <Box
              component="form"
              onSubmit={handleSubmit}
              sx={{ display: "flex", flexDirection: "column", gap: 2 }}
            >
              <TextField
                fullWidth
                label="First Name"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                disabled={loading}
                required
              />
              <TextField
                fullWidth
                label="Last Name"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                disabled={loading}
                required
              />
              <TextField
                fullWidth
                label="Display Name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                disabled={loading}
                helperText="Optional (defaults to First + Last Name)"
              />
              <TextField
                fullWidth
                label="Provider Type"
                select
                value={providerType}
                onChange={(e) => setProviderType(e.target.value)}
                disabled={loading}
              >
                <MenuItem value="email">Email</MenuItem>
                <MenuItem value="phone">Phone</MenuItem>
                <MenuItem value="google">Google</MenuItem>
                <MenuItem value="github">GitHub</MenuItem>
              </TextField>
              <TextField
                fullWidth
                label="Phone"
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                disabled={loading}
                helperText={
                  providerType === "phone"
                    ? "Required for phone sign-up"
                    : "Optional"
                }
              />
              <TextField
                fullWidth
                label="Email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={loading}
                required={providerType !== "phone"}
              />
              <TextField
                fullWidth
                label="Password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loading}
                required
                helperText="At least 6 characters"
                slotProps={{
                  input: {
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          aria-label={
                            showPassword ? "Hide password" : "Show password"
                          }
                          onClick={() => setShowPassword((prev) => !prev)}
                          edge="end"
                        >
                          {showPassword ? <VisibilityOff /> : <Visibility />}
                        </IconButton>
                      </InputAdornment>
                    ),
                  },
                }}
              />
              <TextField
                fullWidth
                label="Confirm Password"
                type={showConfirmPassword ? "text" : "password"}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                disabled={loading}
                required
                slotProps={{
                  input: {
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          aria-label={
                            showConfirmPassword
                              ? "Hide password"
                              : "Show password"
                          }
                          onClick={() => setShowConfirmPassword((prev) => !prev)}
                          edge="end"
                        >
                          {showConfirmPassword ? (
                            <VisibilityOff />
                          ) : (
                            <Visibility />
                          )}
                        </IconButton>
                      </InputAdornment>
                    ),
                  },
                }}
              />
              <Button
                fullWidth
                variant="contained"
                type="submit"
                disabled={loading}
                sx={{ py: 1.5 }}
              >
                {loading ? (
                  <Stack direction="row" spacing={1} alignItems="center">
                    <CircularProgress size={20} color="inherit" />
                    <span>Creating account…</span>
                  </Stack>
                ) : (
                  "Create Account"
                )}
              </Button>
            </Box>

            <Box sx={{ textAlign: "center" }}>
              <Typography variant="body2">
                Already have an account?{" "}
                <MuiLink href="/auth/signin" sx={{ cursor: "pointer" }}>
                  Sign in
                </MuiLink>
              </Typography>
            </Box>
          </Stack>
        </CardContent>
      </Card>
    </Container>
  );
}
