"use client";

import { useRouter } from "next/navigation";
import { Box, Button, Container, Stack, Typography } from "@mui/material";
import { Home as HomeIcon } from "@mui/icons-material";

export default function TipCancelPage() {
  const router = useRouter();

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%)",
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
            boxShadow: "0 20px 60px rgba(0,0,0,0.1)",
          }}
        >
          <Typography sx={{ fontSize: 64 }}>👋</Typography>

          <Typography variant="h4" sx={{ fontWeight: 800, color: "#333" }}>
            No Worries!
          </Typography>

          <Typography
            variant="body1"
            color="text.secondary"
            sx={{ maxWidth: 360, lineHeight: 1.6 }}
          >
            Tips are totally optional. OptionsTaxHub is free to use and
            we&apos;re happy you&apos;re here! You can always come back to leave
            a tip later.
          </Typography>

          <Button
            variant="contained"
            size="large"
            startIcon={<HomeIcon />}
            onClick={() => router.push("/")}
            sx={{
              mt: 2,
              py: 1.5,
              px: 4,
              borderRadius: 2,
              fontWeight: 600,
              textTransform: "none",
            }}
          >
            Back to Dashboard
          </Button>
        </Stack>
      </Container>
    </Box>
  );
}
