"use client";

import NextLink from "next/link";
import { usePathname } from "next/navigation";
import { Box, ButtonBase } from "@mui/material";

export const LAST_DESK_KEY = "oth-last-desk";

export function rememberDesk(desk: "tax" | "options"): void {
  try {
    localStorage.setItem(LAST_DESK_KEY, desk);
  } catch {
    // ignore quota / private mode
  }
}

export function readLastDesk(): "tax" | "options" {
  try {
    return localStorage.getItem(LAST_DESK_KEY) === "options"
      ? "options"
      : "tax";
  } catch {
    return "tax";
  }
}

export function lastDeskHref(): "/dashboard" | "/options" {
  return readLastDesk() === "options" ? "/options" : "/dashboard";
}

export default function DeskSwitcher() {
  const pathname = usePathname() ?? "";
  const active: "tax" | "options" | null = pathname.startsWith("/options")
    ? "options"
    : pathname.startsWith("/dashboard")
      ? "tax"
      : null;

  return (
    <Box
      data-testid="desk-switcher"
      role="navigation"
      aria-label="Desks"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        ml: { xs: 1, sm: 1.75 },
        p: 0.25,
        borderRadius: 999,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <DeskLink desk="tax" href="/dashboard" label="Tax desk" active={active} />
      <DeskLink
        desk="options"
        href="/options"
        label="Options desk"
        active={active}
      />
    </Box>
  );
}

function DeskLink({
  desk,
  href,
  label,
  active,
}: Readonly<{
  desk: "tax" | "options";
  href: string;
  label: string;
  active: "tax" | "options" | null;
}>) {
  const selected = active === desk;
  const short = desk === "tax" ? "Tax" : "Options";

  return (
    <ButtonBase
      component={NextLink}
      href={href}
      data-testid={`desk-switch-${desk}`}
      aria-current={selected ? "page" : undefined}
      aria-label={label}
      onClick={() => rememberDesk(desk)}
      sx={{
        px: { xs: 1, sm: 1.35 },
        py: 0.45,
        borderRadius: 999,
        fontSize: 13,
        fontWeight: 650,
        letterSpacing: "-0.01em",
        textDecoration: "none",
        color: selected ? "primary.contrastText" : "text.secondary",
        bgcolor: selected ? "primary.main" : "transparent",
        "&:hover": {
          bgcolor: selected ? "primary.light" : "action.hover",
        },
      }}
    >
      <Box component="span" sx={{ display: { xs: "inline", sm: "none" } }}>
        {short}
      </Box>
      <Box component="span" sx={{ display: { xs: "none", sm: "inline" } }}>
        {label}
      </Box>
    </ButtonBase>
  );
}
