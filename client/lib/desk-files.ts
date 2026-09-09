"use client";

/**
 * In-memory Files for the current desk session.
 * File cannot live in sessionStorage; a module holder survives
 * /dashboard ↔ /options remounts in the same tab.
 */

export type DeskFiles = {
  csv: File | null;
  form1099: File | null;
};

let csvFile: File | null = null;
let form1099File: File | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach((listener) => listener());
}

export function getDeskFiles(): DeskFiles {
  return { csv: csvFile, form1099: form1099File };
}

export function setDeskCsv(file: File | null): void {
  csvFile = file;
  notify();
}

export function setDeskForm1099(file: File | null): void {
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
