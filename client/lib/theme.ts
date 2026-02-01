import { createTheme } from "@mui/material/styles";

/**
 * Material UI Theme for OptionsTaxHub
 * 
 * Color palette designed for financial trading platform:
 * - Primary (Blue): Professional, trustworthy for financial app
 * - Secondary (Amber): Highlight/warnings for tax optimization opportunities
 * - Error (Red): Loss/risk indicators
 * - Success (Green): Gains/recommendations
 * - Warning (Orange): Tax warnings/considerations
 * 
 * Typography: Uses system fonts for performance (no Google Fonts load)
 */

export const theme = createTheme({
  palette: {
    primary: {
      main: "#1976d2", // Professional blue
      light: "#42a5f5",
      dark: "#1565c0",
    },
    secondary: {
      main: "#f57c00", // Amber/Orange for highlights
      light: "#ffb74d",
      dark: "#e65100",
    },
    success: {
      main: "#2e7d32", // Green for gains
    },
    error: {
      main: "#d32f2f", // Red for losses
    },
    warning: {
      main: "#f57f17", // Orange for warnings
    },
    background: {
      default: "#fafafa",
      paper: "#ffffff",
    },
  },
  typography: {
    fontFamily: [
      "-apple-system",
      "BlinkMacSystemFont",
      '"Segoe UI"',
      "Roboto",
      '"Helvetica Neue"',
      "Arial",
      "sans-serif",
    ].join(","),
    h1: {
      fontSize: "2.5rem",
      fontWeight: 600,
      lineHeight: 1.2,
    },
    h2: {
      fontSize: "2rem",
      fontWeight: 600,
    },
    h3: {
      fontSize: "1.5rem",
      fontWeight: 600,
    },
    body1: {
      fontSize: "1rem",
      lineHeight: 1.5,
    },
  },
  shape: {
    borderRadius: 8, // Slightly rounded for modern look
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 600,
          fontSize: "1rem",
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          boxShadow: "0 2px 4px rgba(0,0,0,0.1)",
        },
      },
    },
  },
});
