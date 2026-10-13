"use client";

import { useEffect, useState } from "react";

/** Read `next` after mount so the server and first client render match. */
export function useQueryNext(): string | null {
  const [next, setNext] = useState<string | null>(null);

  useEffect(() => {
    setNext(new URLSearchParams(window.location.search).get("next"));
  }, []);

  return next;
}
