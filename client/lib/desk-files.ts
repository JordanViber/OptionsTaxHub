"use client";

/**
 * In-memory Files for the current desk session.
 * File cannot live in sessionStorage; a module holder survives
 * /dashboard ↔ /options remounts in the same tab.
 *
 * Not durable: a full reload clears it. Fast Refresh can also
 * reset the module in local dev — treat as session RAM only.
 */

export type DeskFiles = {
  csv: File | null;
  form1099: File | null;
};

let csvFile: File | null = null;
let form1099File: File | null = null;
let snapshot: DeskFiles = { csv: null, form1099: null };
const listeners = new Set<() => void>();

function notify(): void {
  snapshot = { csv: csvFile, form1099: form1099File };
  listeners.forEach((listener) => listener());
}

export function getDeskFiles(): DeskFiles {
  return snapshot;
}

export function getDeskCsv(): File | null {
  return csvFile;
}

export function getDeskForm1099(): File | null {
  return form1099File;
}

export function setDeskCsv(file: File | null): void {
  if (csvFile === file) {
    return;
  }
  csvFile = file;
  notify();
}

export function setDeskForm1099(file: File | null): void {
  if (form1099File === file) {
    return;
  }
  form1099File = file;
  notify();
}

export function subscribeDeskFiles(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function resetDeskFiles(): void {
  csvFile = null;
  form1099File = null;
  notify();
}
