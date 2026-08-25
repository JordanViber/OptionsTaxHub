"use client";

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

export default function CheckEmailCard({
  email,
  title = "Check your email",
  onResend,
  resendLoading = false,
  info,
  error,
}: Readonly<{
  email?: string;
  title?: string;
  onResend?: () => void;
  resendLoading?: boolean;
  info?: string;
  error?: string;
}>) {
  return (
    <Card>
      <CardContent>
        <Stack spacing={3}>
          <Box>
            <Typography variant="h4" sx={{ mb: 1, fontWeight: 700 }}>
              {title}
            </Typography>
            <Typography variant="body2" color="textSecondary">
              {email
                ? `We sent a confirmation link to ${email}. Open that email and confirm your account before you sign in.`
                : "We sent a confirmation link to your email. Open that email and confirm your account before you sign in."}
            </Typography>
          </Box>
          {error ? <Alert severity="error">{error}</Alert> : null}
          {info ? <Alert severity="success">{info}</Alert> : null}
          {onResend ? (
            <Button
              variant="outlined"
              onClick={onResend}
              disabled={resendLoading || !email}
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
          <Box sx={{ textAlign: "center" }}>
            <Typography variant="body2">
              Already confirmed?{" "}
              <MuiLink href="/auth/signin" sx={{ cursor: "pointer" }}>
                Sign in
              </MuiLink>
            </Typography>
          </Box>
        </Stack>
      </CardContent>
    </Card>
  );
}
