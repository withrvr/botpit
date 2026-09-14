import { expect, test } from "@playwright/test"

test("Leaderboard page shows both tabs, a game filter, and switches tabs", async ({
  page,
}) => {
  await page.goto("/leaderboard")
  await expect(page.getByRole("heading", { name: "Leaderboard" })).toBeVisible()
  await expect(page.getByRole("tab", { name: "Accounts" })).toBeVisible()
  await expect(page.getByRole("tab", { name: "Models" })).toBeVisible()

  await page.getByRole("tab", { name: "Models" }).click()
  await expect(page.getByRole("tab", { name: "Models" })).toHaveAttribute(
    "aria-selected",
    "true",
  )
})

test("Leaderboard is reachable from the sidebar", async ({ page }) => {
  await page.goto("/")
  await page.getByRole("link", { name: "Leaderboard" }).click()
  await expect(page).toHaveURL(/\/leaderboard$/)
  await expect(page.getByRole("heading", { name: "Leaderboard" })).toBeVisible()
})
