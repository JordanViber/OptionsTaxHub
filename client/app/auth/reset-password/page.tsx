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
import {
  establishRecoverySession,
  signOut,
  updatePassword,
} from "@/lib/supabase";
import AuthPageShell from "@/app/components/AuthPageShell";

export const dynamic = "force-dynamic";

function mapPasswordError(err: unknown): string {
  const raw =
    err instanceof Error
      ? err.message
      : typeof err === "string"
        ? err
        : "";
  const text = raw.replace(/\+/g, " ").toLowerCase();

  if (
    text.includes("otp_expired") ||
    text.includes("expired") ||
    text.includes("invalid") ||
    text.includes("access_denied") ||
    text.includes("session")
  ) {
    return "This reset link is invalid or has expired. Request a new one from the sign-in page.";
  }
  if (text.includes("same") || text.includes("different from the old")) {
    return "Choose a password that is different from your current password.";
  }
  if (
    text.includes("at least") ||
    text.includes("too short") ||
    text.includes("6 character")
  ) {
    return "Password must be at least 6 characters.";
  }
  if (text.includes("weak") || text.includes("pwned") || text.includes("leaked")) {
    return "That password is too easy to guess. Choose a stronger password.";
  }
  return "Could not update your password. Please try again or request a new reset link.";
}

export default function ResetPasswordPage() {
  const router = useRouter();
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        await establishRecoverySession();
        if (!cancelled) {
          setPhase("ready");
        }
      } catch (err) {
        if (!cancelled) {
          setPhase("error");
          setError(mapPasswordError(err));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

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

    setLoading(true);
    try {
      await updatePassword(password);
      try {
        await signOut();
      } catch {
        // Still send the user to sign-in with the success message.
      }
      router.replace("/auth/signin?reset=success");
    } catch (err) {
      setError(mapPasswordError(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthPageShell>
      <Card>
        <CardContent>
          <Stack spacing={3}>
            <Box>
              <Typography variant="h4" sx={{ mb: 1, fontWeight: 700 }}>
                Set a new password
              </Typography>
              <Typography variant="body2" color="textSecondary">
                Choose a new password for your OptionsTaxHub account
              </Typography>
            </Box>

            {phase === "loading" && (
              <Stack direction="row" spacing={1} alignItems="center">
                <CircularProgress size={20} />
                <Typography variant="body2">Checking reset link…</Typography>
              </Stack>
            )}

            {error && <Alert severity="error">{error}</Alert>}

            {phase === "error" && (
              <Box sx={{ textAlign: "center" }}>
                <Typography variant="body2">
                  <MuiLink href="/auth/signin" sx={{ cursor: "pointer" }}>
                    Back to sign in
                  </MuiLink>
                </Typography>
              </Box>
            )}

            {phase === "ready" && (
              <Box
                component="form"
                onSubmit={handleSubmit}
                sx={{ display: "flex", flexDirection: "column", gap: 2 }}
              >
                <TextField
                  fullWidth
                  label="New password"
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
                  label="Confirm password"
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
                            onClick={() =>
                              setShowConfirmPassword((prev) => !prev)
                            }
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
                      <span>Updating password…</span>
                    </Stack>
                  ) : (
                    "Update password"
                  )}
                </Button>
                <Box sx={{ textAlign: "center" }}>
                  <Typography variant="body2">
                    <MuiLink href="/auth/signin" sx={{ cursor: "pointer" }}>
                      Back to sign in
                    </MuiLink>
                  </Typography>
                </Box>
              </Box>
            )}
          </Stack>
        </CardContent>
      </Card>
    </AuthPageShell>
  );
}
