"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Link as MuiLink,
  Stack,
  Typography,
} from "@mui/material";
import AuthPageShell from "@/app/components/AuthPageShell";
import CheckEmailCard from "@/app/components/CheckEmailCard";
import {
  consumeEmailConfirmLink,
  isEmailConfirmed,
  resendSignupConfirmation,
} from "@/lib/supabase";

export const dynamic = "force-dynamic";

function mapConfirmError(err: unknown): string {
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
    text.includes("access_denied")
  ) {
    return "This confirmation link is invalid or has expired. Request a new one from sign-in.";
  }
  return "Could not confirm your email. Request a new confirmation link from sign-in.";
}

export default function ConfirmEmailPage() {
  const router = useRouter();
  const [phase, setPhase] = useState<
    "loading" | "confirmed" | "check-email" | "error"
  >("loading");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [resendLoading, setResendLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const { session, consumedLink } = await consumeEmailConfirmLink();
        if (cancelled) {
          return;
        }
        const address = session?.user?.email ?? "";
        setEmail(address);
        if (isEmailConfirmed(session?.user)) {
          setPhase("confirmed");
          return;
        }
        if (consumedLink) {
          setPhase("error");
          setError(
            "This confirmation link is invalid or has expired. Request a new one from sign-in.",
          );
          return;
        }
        setPhase("check-email");
      } catch (err) {
        if (!cancelled) {
          setPhase("error");
          setError(mapConfirmError(err));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const handleResend = async () => {
    if (!email.trim()) {
      setError("Enter the email for your account on the sign-in page to resend confirmation.");
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
      {phase === "loading" ? (
        <Card>
          <CardContent>
            <Stack direction="row" spacing={1} alignItems="center">
              <CircularProgress size={20} />
              <Typography variant="body2">Confirming your email…</Typography>
            </Stack>
          </CardContent>
        </Card>
      ) : null}

      {phase === "confirmed" ? (
        <Card>
          <CardContent>
            <Stack spacing={3}>
              <Box>
                <Typography variant="h4" sx={{ mb: 1, fontWeight: 700 }}>
                  Email confirmed
                </Typography>
                <Typography variant="body2" color="textSecondary">
                  Your email is confirmed. Continue to your dashboard, or sign in
                  if you are not already signed in.
                </Typography>
              </Box>
              <Button
                fullWidth
                variant="contained"
                onClick={() => router.push("/dashboard")}
                sx={{ py: 1.5 }}
              >
                Continue to dashboard
              </Button>
              <Box sx={{ textAlign: "center" }}>
                <Typography variant="body2">
                  <MuiLink href="/auth/signin" sx={{ cursor: "pointer" }}>
                    Sign in
                  </MuiLink>
                </Typography>
              </Box>
            </Stack>
          </CardContent>
        </Card>
      ) : null}

      {phase === "check-email" ? (
        <CheckEmailCard
          email={email}
          onResend={email ? handleResend : undefined}
          resendLoading={resendLoading}
          info={info}
          error={error}
        />
      ) : null}

      {phase === "error" ? (
        <Card>
          <CardContent>
            <Stack spacing={3}>
              <Box>
                <Typography variant="h4" sx={{ mb: 1, fontWeight: 700 }}>
                  Confirm your email
                </Typography>
              </Box>
              {error ? <Alert severity="error">{error}</Alert> : null}
              {email ? (
                <Button
                  variant="outlined"
                  onClick={handleResend}
                  disabled={resendLoading}
                  sx={{ py: 1.5 }}
                >
                  {resendLoading
                    ? "Sending confirmation email…"
                    : "Resend confirmation email"}
                </Button>
              ) : null}
              <Box sx={{ textAlign: "center" }}>
                <Typography variant="body2">
                  <MuiLink href="/auth/signin" sx={{ cursor: "pointer" }}>
                    Back to sign in
                  </MuiLink>
                </Typography>
              </Box>
            </Stack>
          </CardContent>
        </Card>
      ) : null}
    </AuthPageShell>
  );
}
