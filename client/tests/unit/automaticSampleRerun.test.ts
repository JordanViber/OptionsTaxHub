import {
  consumeAutomaticSampleRerun,
  resetAutomaticSampleRerunForTests,
} from "../../lib/automaticSampleRerun";

describe("automaticSampleRerun", () => {
  beforeEach(() => {
    resetAutomaticSampleRerunForTests();
  });

  it("allows one automatic sample re-run", () => {
    expect(consumeAutomaticSampleRerun()).toBe(true);
    expect(consumeAutomaticSampleRerun()).toBe(false);
    resetAutomaticSampleRerunForTests();
    expect(consumeAutomaticSampleRerun()).toBe(true);
  });
});
