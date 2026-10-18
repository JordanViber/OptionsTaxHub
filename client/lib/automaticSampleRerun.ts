let automaticSampleRerunUsed = false;

/** One automatic sample re-run per page lifetime. A full load starts fresh. */
export function consumeAutomaticSampleRerun(): boolean {
  if (automaticSampleRerunUsed) {
    return false;
  }
  automaticSampleRerunUsed = true;
  return true;
}

/** Tests only. */
export function resetAutomaticSampleRerunForTests(): void {
  automaticSampleRerunUsed = false;
}
