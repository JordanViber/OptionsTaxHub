"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
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
} from "@mui/material";
import { Visibility, VisibilityOff } from "@mui/icons-material";
import { useAuth } from "@/app/context/auth";
import { resetPasswordForEmail } from "@/lib/supabase";
import AuthPageShell from "@/app/components/AuthPageShell";

export const dynamic = "force-dynamic";

export default function SignInPage() {
  const router = useRouter();
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [loading, setLoading] = useState(false);
  const [resetLoading, setResetLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [resetMode, setResetMode] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("reset") === "success") {
      setInfo("Your password was updated. Sign in with your new password.");
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setInfo("");
    setLoading(true);

    try {
      await signIn(email, password);
      router.push("/dashboard");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to sign in. Please try again.",
      );
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setInfo("");

    if (!email.trim()) {
      setError("Enter the email for your account to receive a reset link.");
      return;
    }

    setResetLoading(true);
    try {
      await resetPasswordForEmail(email.trim());
      setInfo("Check your email for a link to set a new password.");
      setResetMode(false);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not send a reset email. Please try again.",
      );
    } finally {
      setResetLoading(false);
    }
  };

  return (
    <AuthPageShell>
      <Card>
        <CardContent>
          <Stack spacing={3}>
            <Box>
              <Typography variant="h4" sx={{ mb: 1, fontWeight: 700 }}>
                Sign In
              </Typography>
              <Typography variant="body2" color="textSecondary">
                {resetMode
                  ? "We'll email you a link to set a new password."
                  : "Welcome back to OptionsTaxHub"}
              </Typography>
            </Box>

            {error && <Alert severity="error">{error}</Alert>}
            {info && <Alert severity="success">{info}</Alert>}

            <Box
              component="form"
              onSubmit={resetMode ? handleReset : handleSubmit}
              sx={{ display: "flex", flexDirection: "column", gap: 2 }}
            >
              <TextField
                fullWidth
                label="Email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={loading || resetLoading}
                required
              />
              {!resetMode && (
                <TextField
                  fullWidth
                  label="Password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                  required
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
              )}
              <Button
                fullWidth
                variant="contained"
                type="submit"
                disabled={loading || resetLoading}
                sx={{ py: 1.5 }}
              >
                {loading || resetLoading ? (
                  <Stack direction="row" spacing={1} alignItems="center">
                    <CircularProgress size={20} color="inherit" />
                    <span>
                      {resetMode ? "Sending reset link…" : "Signing in…"}
                    </span>
                  </Stack>
                ) : resetMode ? (
                  "Send reset link"
                ) : (
                  "Sign In"
                )}
              </Button>
              <Button
                type="button"
                variant="text"
                onClick={() => {
                  setResetMode((prev) => !prev);
                  setError("");
                  setInfo("");
                }}
                disabled={loading || resetLoading}
                sx={{ textTransform: "none" }}
              >
                {resetMode ? "Back to sign in" : "Forgot password?"}
              </Button>
            </Box>

            <Box sx={{ textAlign: "center" }}>
              <Typography variant="body2">
                Don&apos;t have an account?{" "}
                <MuiLink href="/auth/signup" sx={{ cursor: "pointer" }}>
                  Sign up
                </MuiLink>
              </Typography>
            </Box>
          </Stack>
        </CardContent>
      </Card>
    </AuthPageShell>
  );
}
