"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Box, Button, Container, Stack, Typography } from "@mui/material";
import {
  CheckCircleOutline as SuccessIcon,
  Home as HomeIcon,
} from "@mui/icons-material";

export default function TipSuccessPage() {
  const router = useRouter();
  const [countdown, setCountdown] = useState(8);

  // Auto-redirect to dashboard after countdown
  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          router.push("/dashboard");
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [router]);

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
      }}
    >
      <Container maxWidth="sm">
        <Stack
          alignItems="center"
          spacing={3}
          sx={{
            textAlign: "center",
            backgroundColor: "white",
            borderRadius: 4,
            py: 6,
            px: 4,
            boxShadow: "0 20px 60px rgba(0,0,0,0.3)",
          }}
        >
          <SuccessIcon sx={{ fontSize: 80, color: "#4caf50" }} />

          <Typography variant="h4" sx={{ fontWeight: 800, color: "#333" }}>
            Thank You! 💜
          </Typography>

          <Typography
            variant="body1"
            color="text.secondary"
            sx={{ maxWidth: 360, lineHeight: 1.6 }}
          >
            Your generosity means the world to us. Every tip helps keep
            OptionsTaxHub free and improving for everyone.
          </Typography>

          <Typography
            variant="h6"
            sx={{
              fontWeight: 600,
              color: "#764ba2",
              fontSize: "1.1rem",
            }}
          >
            You&apos;re officially awesome! 🎉
          </Typography>

          <Button
            variant="contained"
            size="large"
            startIcon={<HomeIcon />}
            onClick={() => router.push("/dashboard")}
            sx={{
              mt: 2,
              py: 1.5,
              px: 4,
              borderRadius: 2,
              fontWeight: 600,
              textTransform: "none",
              background: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
              "&:hover": {
                background: "linear-gradient(135deg, #5a6fd6 0%, #6a4296 100%)",
              },
            }}
          >
            Back to Dashboard
          </Button>

          <Typography variant="caption" color="text.secondary">
            Redirecting in {countdown} seconds...
          </Typography>
        </Stack>
      </Container>
    </Box>
  );
}
