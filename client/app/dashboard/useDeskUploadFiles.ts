"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getDeskCsv,
  getDeskForm1099,
  resetDeskFiles,
  setDeskCsv,
  setDeskForm1099,
  subscribeDeskFiles,
} from "@/lib/desk-files";

export function useDeskUploadFiles() {
  const [lastUploadedCsv, setCsvState] = useState<File | null>(getDeskCsv);
  const [supplemental1099File, setForm1099State] =
    useState<File | null>(getDeskForm1099);

  useEffect(() => {
    return subscribeDeskFiles(() => {
      setCsvState(getDeskCsv());
      setForm1099State(getDeskForm1099());
    });
  }, []);

  const setLastUploadedCsv = useCallback((file: File | null) => {
    setDeskCsv(file);
    setCsvState(file);
  }, []);

  const setSupplemental1099File = useCallback((file: File | null) => {
    setDeskForm1099(file);
    setForm1099State(file);
  }, []);

  return {
    lastUploadedCsv,
    setLastUploadedCsv,
    supplemental1099File,
    setSupplemental1099File,
    resetDeskFiles,
  };
}
