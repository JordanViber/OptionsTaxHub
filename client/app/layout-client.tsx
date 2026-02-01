"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import { queryClient } from "@/lib/queryClient";
import { theme } from "@/lib/theme";

/**
 * Client-side layout wrapper
 * Provides React Query and Material UI theming context
 * This is separate from the root layout to allow metadata exports
 */
export default function RootLayoutClient({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
