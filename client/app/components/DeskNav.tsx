"use client";

import { Button, Stack } from "@mui/material";
import NextLink from "next/link";
import { usePathname } from "next/navigation";
import { OPTIONS_PATH, TAX_PATH } from "@/lib/safeNextPath";

const NAV_ITEMS = [
  { href: TAX_PATH, label: "Tax" },
  { href: OPTIONS_PATH, label: "Options" },
] as const;

/**
 * Tax and Options stay visible on desktop and on a ~390px phone row.
 */
export default function DeskNav() {
  const pathname = usePathname() || "";

  return (
    <Stack
      component="nav"
      aria-label="Primary"
      direction="row"
      spacing={0.5}
      useFlexGap
      sx={{
        px: { xs: 1, sm: 2 },
        pb: 1,
        flexWrap: "wrap",
      }}
    >
      {NAV_ITEMS.map((item) => {
        const active =
          pathname === item.href || pathname.startsWith(`${item.href}/`);
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
              minWidth: 72,
              px: 1.25,
            }}
          >
            {item.label}
          </Button>
        );
      })}
    </Stack>
  );
}
