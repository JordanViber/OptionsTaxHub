"use client";

import { useState } from "react";
import {
  Box,
  Card,
  CardActionArea,
  CardContent,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import {
  Close as CloseIcon,
  FavoriteBorder as HeartOutlineIcon,
  Favorite as HeartIcon,
  LocalCafe as CoffeeIcon,
  Restaurant as LunchIcon,
  VolunteerActivism as GenerousIcon,
} from "@mui/icons-material";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

interface TipTier {
  id: string;
  label: string;
  amount: number;
  icon: React.ReactNode;
  emoji: string;
  color: string;
  description: string;
}

const TIP_TIERS: TipTier[] = [
  {
    id: "coffee",
    label: "Coffee",
    amount: 300,
    icon: <CoffeeIcon sx={{ fontSize: 40 }} />,
    emoji: "☕",
    color: "#8B4513",
    description: "Buy us a coffee",
  },
  {
    id: "lunch",
    label: "Lunch",
    amount: 1000,
    icon: <LunchIcon sx={{ fontSize: 40 }} />,
    emoji: "🍕",
    color: "#e65100",
    description: "Buy us lunch",
  },
  {
    id: "generous",
    label: "Generous",
    amount: 2500,
    icon: <GenerousIcon sx={{ fontSize: 40 }} />,
    emoji: "💝",
    color: "#c62828",
    description: "You're amazing!",
  },
];

function formatAmount(cents: number): string {
  return `$${(cents / 100).toFixed(0)}`;
}

interface TipJarProps {
  open: boolean;
  onClose: () => void;
}

export default function TipJar({ open, onClose }: Readonly<TipJarProps>) {
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleTip = async (tierId: string) => {
    setSelected(tierId);
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_URL}/api/tips/checkout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier: tierId }),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => null);
        const detail = errData?.detail || "Failed to create checkout session";
        throw new Error(detail);
      }

      const data = await response.json();
      // Redirect to Stripe Checkout
      globalThis.location.href = data.checkout_url;
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Something went wrong",
      );
      setLoading(false);
      setSelected(null);
    }
  };

  const handleClose = () => {
    if (!loading) {
      setSelected(null);
      setError(null);
      onClose();
    }
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      slotProps={{
        paper: {
          sx: {
            borderRadius: 3,
            overflow: "hidden",
          },
        },
      }}
    >
      {/* Gradient header */}
      <Box
        sx={{
          background: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
          py: 3,
          px: 3,
          position: "relative",
        }}
      >
        <IconButton
          onClick={handleClose}
          disabled={loading}
          sx={{
            position: "absolute",
            top: 8,
            right: 8,
            color: "white",
            "&:hover": { backgroundColor: "rgba(255,255,255,0.15)" },
          }}
          aria-label="close"
        >
          <CloseIcon />
        </IconButton>

        <Stack alignItems="center" spacing={1}>
          <HeartIcon
            sx={{
              fontSize: 48,
              color: "#ff6b6b",
              filter: "drop-shadow(0 2px 4px rgba(0,0,0,0.3))",
            }}
          />
          <DialogTitle
            sx={{
              color: "white",
              fontWeight: 800,
              fontSize: "1.5rem",
              textAlign: "center",
              p: 0,
            }}
          >
            Support OptionsTaxHub
          </DialogTitle>
          <Typography
            variant="body2"
            sx={{
              color: "rgba(255,255,255,0.85)",
              textAlign: "center",
              maxWidth: 320,
            }}
          >
            If this tool has helped you save on taxes, consider leaving a tip!
            Every bit helps keep us running.
          </Typography>
        </Stack>
      </Box>

      <DialogContent sx={{ py: 3, px: 3 }}>
        <Stack spacing={2}>
          {/* Tip tier cards */}
          <Stack
            direction="row"
            spacing={2}
            justifyContent="center"
          >
            {TIP_TIERS.map((tier) => {
              const isSelected = selected === tier.id;
              const isDisabled = loading && !isSelected;

              return (
                <Card
                  key={tier.id}
                  elevation={isSelected ? 8 : 2}
                  sx={{
                    flex: 1,
                    maxWidth: 160,
                    border: 2,
                    borderColor: isSelected ? tier.color : "transparent",
                    transition: "all 0.2s ease",
                    opacity: isDisabled ? 0.5 : 1,
                    "&:hover": isDisabled
                      ? {}
                      : {
                          transform: "translateY(-4px)",
                          boxShadow: 6,
                          borderColor: tier.color,
                        },
                  }}
                >
                  <CardActionArea
                    onClick={() => handleTip(tier.id)}
                    disabled={loading}
                    sx={{ height: "100%" }}
                  >
                    <CardContent
                      sx={{
                        textAlign: "center",
                        py: 2.5,
                        px: 1.5,
                      }}
                    >
                      {isSelected && loading ? (
                        <CircularProgress
                          size={40}
                          sx={{ color: tier.color, mb: 1 }}
                        />
                      ) : (
                        <Box
                          sx={{
                            color: tier.color,
                            mb: 1,
                            display: "flex",
                            justifyContent: "center",
                          }}
                        >
                          {tier.icon}
                        </Box>
                      )}
                      <Typography
                        variant="h5"
                        sx={{
                          fontWeight: 800,
                          color: tier.color,
                          mb: 0.5,
                        }}
                      >
                        {formatAmount(tier.amount)}
                      </Typography>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ fontWeight: 500 }}
                      >
                        {tier.description}
                      </Typography>
                    </CardContent>
                  </CardActionArea>
                </Card>
              );
            })}
          </Stack>

          {/* Error message */}
          {error && (
            <Typography
              variant="body2"
              color="error"
              textAlign="center"
              role="alert"
            >
              {error}
            </Typography>
          )}

          {/* Footer */}
          <Stack alignItems="center" spacing={0.5} sx={{ pt: 1 }}>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <HeartOutlineIcon
                sx={{ fontSize: 14, color: "text.secondary" }}
              />
              <Typography
                variant="caption"
                color="text.secondary"
              >
                Payments processed securely by Stripe
              </Typography>
            </Stack>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ opacity: 0.7 }}
            >
              One-time payment &middot; No subscription
            </Typography>
          </Stack>
        </Stack>
      </DialogContent>
    </Dialog>
  );
}
