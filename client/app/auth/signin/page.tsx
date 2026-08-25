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
import {
  getSession,
  isEmailConfirmed,
  isEmailNotConfirmedError,
  resetPasswordForEmail,
  resendSignupConfirmation,
} from "@/lib/supabase";
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
  const [resendLoading, setResendLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [resetMode, setResetMode] = useState(false);
  const [needsConfirmation, setNeedsConfirmation] = useState(false);

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
    setNeedsConfirmation(false);
    setLoading(true);

    try {
      await signIn(email, password);
      const session = await getSession();
      if (session?.user && !isEmailConfirmed(session.user)) {
        setNeedsConfirmation(true);
        setError(
          "Confirm your email before opening the dashboard. We can send another confirmation link.",
        );
        return;
      }
      router.push("/dashboard");
    } catch (err) {
      if (isEmailNotConfirmedError(err)) {
        setNeedsConfirmation(true);
        setError(
          "Confirm your email before signing in. We can send another confirmation link.",
        );
      } else {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to sign in. Please try again.",
        );
      }
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

  const handleResendConfirm = async () => {
    if (!email.trim()) {
      setError("Enter the email for your account to resend confirmation.");
      return;
    }
    setError("");
    setInfo("");
    setResendLoading(true);
    try {
      await resendSignupConfirmation(email.trim());
      setInfo("Another confirmation email is on the way. Check your inbox.");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not resend the confirmation email. Please try again.",
      );
    } finally {
      setResendLoading(false);
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

            {needsConfirmation && !resetMode ? (
              <Button
                type="button"
                variant="outlined"
                onClick={handleResendConfirm}
                disabled={loading || resendLoading}
                sx={{ py: 1.5 }}
              >
                {resendLoading ? (
                  <Stack direction="row" spacing={1} alignItems="center">
                    <CircularProgress size={20} color="inherit" />
                    <span>Sending confirmation email…</span>
                  </Stack>
                ) : (
                  "Resend confirmation email"
                )}
              </Button>
            ) : null}

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
                disabled={loading || resetLoading || resendLoading}
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
                disabled={loading || resetLoading || resendLoading}
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
                  setNeedsConfirmation(false);
                }}
                disabled={loading || resetLoading || resendLoading}
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
