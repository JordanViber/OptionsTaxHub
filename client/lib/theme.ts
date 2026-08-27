import { createTheme } from "@mui/material/styles";

/**
 * Ink / bone desk theme for OptionsTaxHub.
 *
 * Dark trading-desk surface with bone CTAs, cream type, and gain/loss inks.
 * Not a generic blue SaaS palette.
 */
export const theme = createTheme({
  palette: {
    mode: "dark",
    primary: {
      main: "#d8d2c6",
      light: "#eceae4",
      dark: "#b7b0a3",
      contrastText: "#0c0d10",
    },
    secondary: {
      main: "#7a9e84",
      light: "#9bb8a3",
      dark: "#5c7a64",
      contrastText: "#090a0c",
    },
    success: {
      main: "#7a9e84",
      dark: "#1c2920",
    },
    error: {
      main: "#c46a58",
      dark: "#2a1816",
    },
    warning: {
      main: "#c4a36a",
      dark: "#2a2316",
    },
    info: {
      main: "#8e9088",
    },
    background: {
      default: "#090a0c",
      paper: "#12141a",
    },
    text: {
      primary: "#eceae4",
      secondary: "#8e9088",
    },
    divider: "#2a2d34",
    action: {
      hover: "rgba(236,234,228,0.06)",
      selected: "rgba(216,210,198,0.12)",
    },
  },
  typography: {
    fontFamily: [
      "var(--font-sans)",
      "Figtree",
      "-apple-system",
      "BlinkMacSystemFont",
      '"Segoe UI"',
      "sans-serif",
    ].join(","),
    h1: {
      fontFamily: "var(--font-display), Fraunces, Georgia, serif",
      fontSize: "2.5rem",
      fontWeight: 600,
      lineHeight: 1.15,
      letterSpacing: "-0.03em",
    },
    h2: {
      fontFamily: "var(--font-display), Fraunces, Georgia, serif",
      fontSize: "2rem",
      fontWeight: 600,
      letterSpacing: "-0.03em",
    },
    h3: {
      fontFamily: "var(--font-display), Fraunces, Georgia, serif",
      fontSize: "1.5rem",
      fontWeight: 600,
      letterSpacing: "-0.02em",
    },
    h4: {
      fontFamily: "var(--font-display), Fraunces, Georgia, serif",
      fontWeight: 600,
      letterSpacing: "-0.02em",
    },
    h5: {
      fontFamily: "var(--font-display), Fraunces, Georgia, serif",
      fontWeight: 600,
    },
    h6: {
      fontWeight: 700,
    },
    button: {
      fontWeight: 600,
    },
    body1: {
      fontSize: "1rem",
      lineHeight: 1.55,
    },
  },
  shape: {
    borderRadius: 10,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 600,
          fontSize: "0.95rem",
          borderRadius: 10,
        },
        contained: {
          boxShadow: "none",
          "&:hover": { boxShadow: "none" },
        },
        outlined: {
          borderColor: "#2a2d34",
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          backgroundColor: "#12141a",
          boxShadow: "0 0 0 1px rgb(236 234 228 / 0.08)",
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          backgroundColor: "#090a0c",
          color: "#eceae4",
          borderBottom: "1px solid #2a2d34",
          boxShadow: "none",
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          fontWeight: 600,
        },
      },
    },
  },
});
