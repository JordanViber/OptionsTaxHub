const inFlight = new Set<string>();

/** Returns false if this guest snapshot is already being saved in this JS context. */
export function beginGuestPersist(persistKey: string): boolean {
  if (!persistKey || inFlight.has(persistKey)) {
    return false;
  }
  inFlight.add(persistKey);
  return true;
}

export function endGuestPersist(persistKey: string): void {
  inFlight.delete(persistKey);
}

/** Tests only — a full reload already gets a new Set. */
export function resetGuestPersistInFlight(): void {
  inFlight.clear();
}
