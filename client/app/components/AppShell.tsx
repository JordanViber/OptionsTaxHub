"use client";

import { useEffect, useState } from "react";
import {
  AppBar,
  Avatar,
  Box,
  Button,
  Menu,
  MenuItem,
  Toolbar,
  Typography,
  useMediaQuery,
} from "@mui/material";
import {
  CloudUpload as CloudUploadIcon,
  History as HistoryIcon,
  Login as LoginIcon,
  Logout as LogoutIcon,
  Menu as MenuIcon,
} from "@mui/icons-material";
import NextLink from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/app/context/auth";
import InstallPrompt from "./InstallPrompt";
import TipJar from "./TipJar";
import Wordmark from "./Wordmark";
import {
  OPTIONS_PATH,
  TAX_PATH,
  rememberOpenRuns,
  rememberSampleIntent,
  rememberUploadIntent,
  signInHref,
} from "@/lib/safeNextPath";

const NAV_ITEMS = [
  { href: TAX_PATH, label: "Tax" },
  { href: OPTIONS_PATH, label: "Options" },
] as const;

/** Desktop actions stay in the toolbar only above 900px. 600–900px uses the menu. */
export const COMPACT_HEADER_QUERY = "(max-width:900px)";

const ANALYSIS_STORAGE_KEY = "optionstaxhub-analysis";

type ShellVariant = "app" | "landing" | "auth";

type AccountLike = {
  email?: string | null;
  user_metadata?: {
    first_name?: string | null;
    last_name?: string | null;
    display_name?: string | null;
    full_name?: string | null;
  } | null;
} | null;

export function accountDisplayName(user: AccountLike): string {
  if (!user) {
    return "Account";
  }
  const meta = user.user_metadata ?? {};
  const fullName = [meta.first_name, meta.last_name].filter(Boolean).join(" ");
  return (
    meta.display_name || meta.full_name || fullName || user.email || "Account"
  );
}

function pathIsActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

const actionButtonSx = {
  textTransform: "none",
  whiteSpace: "nowrap",
  flexShrink: 0,
  fontWeight: 700,
} as const;

/**
 * Shared header for marketing, auth, and app pages.
 * Tax and Options stay one tap away at 390px. Other actions sit in a labeled menu.
 */
export default function AppShell({
  variant = "app",
  children,
  onUpload,
  onSavedRuns,
}: Readonly<{
  variant?: ShellVariant;
  children?: React.ReactNode;
  onUpload?: () => void;
  onSavedRuns?: () => void;
}>) {
  const pathname = usePathname() || "/";
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user, signOut } = useAuth();
  const matchesCompact = useMediaQuery(COMPACT_HEADER_QUERY);
  // SSR and the first client render omit the wide action row. Painting it
  // before matchMedia runs overflows a 390px header on fast pages.
  const [viewportReady, setViewportReady] = useState(false);
  useEffect(() => {
    setViewportReady(true);
  }, []);
  const compact = viewportReady && matchesCompact;
  const showFullActions = viewportReady && !matchesCompact;
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [accountAnchor, setAccountAnchor] = useState<HTMLElement | null>(null);
  const [tipOpen, setTipOpen] = useState(false);

  const displayName = accountDisplayName(user);
  const avatarLetter = displayName[0]?.toUpperCase() || "A";
  const showAppActions = variant === "app";
  const showLandingActions = variant === "landing";
  const closeMenus = () => {
    setMenuAnchor(null);
    setAccountAnchor(null);
  };

  const goSample = () => {
    closeMenus();
    rememberSampleIntent();
    router.push(TAX_PATH);
  };

  const goUpload = () => {
    closeMenus();
    if (onUpload) {
      onUpload();
      return;
    }
    rememberUploadIntent();
    if (pathname !== TAX_PATH) {
      router.push(TAX_PATH);
    }
  };

  const goSavedRuns = () => {
    closeMenus();
    if (!user) {
      router.push(signInHref(pathname, "runs"));
      return;
    }
    if (onSavedRuns) {
      onSavedRuns();
      return;
    }
    rememberOpenRuns();
    if (pathname !== TAX_PATH) {
      router.push(TAX_PATH);
    }
  };

  const goSettings = () => {
    closeMenus();
    if (!user) {
      router.push(signInHref(pathname, "profile"));
      return;
    }
    router.push("/settings");
  };

  const goSignOut = async () => {
    closeMenus();
    const userId = user?.id;
    try {
      sessionStorage.removeItem(ANALYSIS_STORAGE_KEY);
    } catch {
      // ignore
    }
    // Drop this account's cached runs and tax profile before the next sign-in.
    queryClient.removeQueries({ queryKey: ["portfolio-history", userId] });
    queryClient.removeQueries({ queryKey: ["tax-profile", userId] });
    try {
      if (typeof caches !== "undefined") {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      }
    } catch {
      // ignore
    }
    await signOut();
    router.push("/auth/signin");
  };

  const openTip = () => {
    closeMenus();
    setTipOpen(true);
  };

  const filled = variant === "app";

  return (
    <Box
      sx={{
        minHeight: variant === "auth" ? "100vh" : undefined,
        bgcolor: variant === "auth" ? "background.default" : undefined,
      }}
    >
      <AppBar
        component="header"
        position="static"
        elevation={filled ? 4 : 0}
        data-testid="app-header"
        sx={
          filled
            ? { zIndex: 40 }
            : {
                position: "relative",
                zIndex: 30,
                bgcolor: "transparent",
                color: "text.primary",
                backgroundImage: "none",
                boxShadow: "none",
              }
        }
      >
        <Toolbar
          sx={{
            px: { xs: 1, sm: 2 },
            gap: 0.75,
            flexWrap: "nowrap",
            minWidth: 0,
            minHeight: 64,
            height: 64,
            maxHeight: 64,
          }}
          style={{ flexWrap: "nowrap", height: 64, maxHeight: 64 }}
        >
          <Wordmark href="/" />
          <Box
            component="nav"
            aria-label="Primary"
            data-testid="primary-nav"
            sx={{ display: "flex", alignItems: "center", gap: 0.5, flexShrink: 0 }}
          >
            {NAV_ITEMS.map((item) => {
              const active = pathIsActive(pathname, item.href);
              return (
                <Button
                  key={item.href}
                  component={NextLink}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  size="small"
                  variant={active ? "contained" : "text"}
                  color="inherit"
                  sx={{
                    textTransform: "none",
                    fontWeight: 700,
                    minWidth: 64,
                    px: 1.25,
                    flexShrink: 0,
                  }}
                >
                  {item.label}
                </Button>
              );
            })}
          </Box>
          <Box sx={{ flexGrow: 1 }} />
          {showFullActions && (showLandingActions || showAppActions) ? (
            <Box
              data-testid="header-actions"
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                flexShrink: 0,
              }}
            >
              {showLandingActions ? (
                <Button
                  variant="contained"
                  size="small"
                  data-testid="header-try-sample"
                  onClick={goSample}
                  sx={actionButtonSx}
                >
                  Try the sample
                </Button>
              ) : null}
              {showAppActions ? (
                <Button
                  color="inherit"
                  aria-label="Upload"
                  data-testid="header-upload"
                  startIcon={<CloudUploadIcon />}
                  onClick={goUpload}
                  sx={actionButtonSx}
                >
                  Upload
                </Button>
              ) : null}
              {showAppActions ? (
                <Button
                  color="inherit"
                  aria-label="Saved runs"
                  data-testid="header-saved-runs"
                  startIcon={<HistoryIcon />}
                  onClick={goSavedRuns}
                  sx={actionButtonSx}
                >
                  Saved runs
                </Button>
              ) : null}
              {user ? (
                <Button
                  color="inherit"
                  aria-label={displayName}
                  data-testid="header-account"
                  onClick={(event) => setAccountAnchor(event.currentTarget)}
                  sx={{ ...actionButtonSx, minWidth: "auto", maxWidth: 220, px: 1 }}
                >
                  <Avatar sx={{ width: 32, height: 32, flexShrink: 0 }}>
                    {avatarLetter}
                  </Avatar>
                  <Typography
                    component="span"
                    data-testid="header-account-name"
                    sx={{
                      ml: 1,
                      maxWidth: 160,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      display: "block",
                    }}
                  >
                    {displayName}
                  </Typography>
                </Button>
              ) : (
                <Button
                  component={NextLink}
                  href={signInHref(pathname)}
                  aria-label="Sign in"
                  data-testid="header-sign-in"
                  variant={variant === "landing" ? "text" : "contained"}
                  size="small"
                  color="inherit"
                  startIcon={<LoginIcon />}
                  sx={actionButtonSx}
                >
                  Sign in
                </Button>
              )}
            </Box>
          ) : null}
          {compact && variant !== "auth" ? (
            <Button
              color="inherit"
              aria-label="Menu"
              data-testid="header-menu"
              startIcon={<MenuIcon />}
              onClick={(event) => setMenuAnchor(event.currentTarget)}
              sx={actionButtonSx}
            >
              Menu
            </Button>
          ) : null}
        </Toolbar>
      </AppBar>

      <InstallPrompt />

      {compact && variant !== "auth" ? (
        <Menu
          anchorEl={menuAnchor}
          open={Boolean(menuAnchor)}
          onClose={closeMenus}
          slotProps={{ paper: { sx: { zIndex: 50, minWidth: 220 } } }}
        >
          {NAV_ITEMS.map((item) => {
            const active = pathIsActive(pathname, item.href);
            return (
              <MenuItem
                key={item.href}
                component={NextLink}
                href={item.href}
                selected={active}
                aria-current={active ? "page" : undefined}
                onClick={closeMenus}
              >
                {active ? `${item.label} (current)` : item.label}
              </MenuItem>
            );
          })}
          {showLandingActions ? (
            <MenuItem data-testid="header-try-sample" onClick={goSample}>
              Try the sample
            </MenuItem>
          ) : null}
          {showAppActions ? (
            <MenuItem data-testid="header-upload" onClick={goUpload}>
              Upload
            </MenuItem>
          ) : null}
          {showAppActions ? (
            <MenuItem data-testid="header-saved-runs" onClick={goSavedRuns}>
              Saved runs
            </MenuItem>
          ) : null}
          {user ? (
            <MenuItem disabled data-testid="header-account">
              <Typography variant="body2">
                {displayName}
                {user.email ? ` (${user.email})` : ""}
              </Typography>
            </MenuItem>
          ) : (
            <MenuItem
              component={NextLink}
              href={signInHref(pathname)}
              data-testid="header-sign-in"
              onClick={closeMenus}
            >
              Sign in
            </MenuItem>
          )}
          {user ? (
            <MenuItem data-testid="account-settings" onClick={goSettings}>
              Settings
            </MenuItem>
          ) : null}
          {user ? <MenuItem onClick={openTip}>Tip</MenuItem> : null}
          {user ? (
            <MenuItem onClick={goSignOut}>
              <LogoutIcon sx={{ mr: 1 }} />
              Sign Out
            </MenuItem>
          ) : null}
        </Menu>
      ) : null}

      {!compact && user ? (
        <Menu
          anchorEl={accountAnchor}
          open={Boolean(accountAnchor)}
          onClose={closeMenus}
          slotProps={{ paper: { sx: { zIndex: 50 } } }}
        >
          <MenuItem disabled>
            <Typography variant="body2">
              {displayName}
              {user.email ? ` (${user.email})` : ""}
            </Typography>
          </MenuItem>
          <MenuItem onClick={goSavedRuns}>Saved runs</MenuItem>
          <MenuItem data-testid="account-settings" onClick={goSettings}>
            Settings
          </MenuItem>
          <MenuItem onClick={openTip}>Tip</MenuItem>
          <MenuItem onClick={goSignOut}>
            <LogoutIcon sx={{ mr: 1 }} />
            Sign Out
          </MenuItem>
        </Menu>
      ) : null}

      {variant !== "auth" ? (
        <TipJar open={tipOpen} onClose={() => setTipOpen(false)} />
      ) : null}
      {children}
    </Box>
  );
}
