import { test, expect } from '@playwright/test';

test('homepage renders primary UI', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveTitle(/OptionsTaxHub/i);
  await expect(
    page.getByRole('heading', {
      name: /OptionsTaxHub – Tax-Optimized Options Trading/i,
    })
  ).toBeVisible();
  await expect(page.getByRole('button', { name: /upload csv/i })).toBeDisabled();
});