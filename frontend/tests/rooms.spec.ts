import { expect, test } from "@playwright/test"
import { createUser } from "./utils/privateApi"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser } from "./utils/user"

test("Lobby is accessible and shows the create/join controls", async ({
  page,
}) => {
  await page.goto("/")
  await expect(page.getByRole("heading", { name: "The lobby" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Create room" })).toBeVisible()
  await expect(page.getByPlaceholder("Join code")).toBeVisible()
})

test.describe("Create and play a room", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Creating a private 2-seat room shows both seats waiting", async ({
    page,
  }) => {
    const email = randomEmail()
    const password = randomPassword()
    await createUser({ email, password })
    await logInUser(page, email, password)

    await page.getByRole("button", { name: "Create room" }).click()
    await page.getByRole("button", { name: "+" }).click() // 2 -> 3 seats, then back down
    await page.getByRole("button", { name: "-" }).click() // back to 2, exercising both controls
    await page.getByRole("button", { name: "Create", exact: true }).click()

    await expect(page.getByText(/^Code /)).toBeVisible()
    await expect(page.getByText("Seat 0")).toBeVisible()
    await expect(page.getByText("Seat 1")).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Start", exact: true }),
    ).toBeVisible()
  })

  test("Two human players can join, start, and play a real move", async ({
    browser,
  }) => {
    const creatorEmail = randomEmail()
    const creatorPassword = randomPassword()
    const joinerEmail = randomEmail()
    const joinerPassword = randomPassword()
    await createUser({ email: creatorEmail, password: creatorPassword })
    await createUser({ email: joinerEmail, password: joinerPassword })

    const creatorContext = await browser.newContext()
    const joinerContext = await browser.newContext()
    const creatorPage = await creatorContext.newPage()
    const joinerPage = await joinerContext.newPage()

    await logInUser(creatorPage, creatorEmail, creatorPassword)
    await logInUser(joinerPage, joinerEmail, joinerPassword)

    await creatorPage.getByRole("button", { name: "Create room" }).click()
    await creatorPage
      .getByRole("button", { name: "Create", exact: true })
      .click()

    const codeText = await creatorPage.getByText(/^Code /).textContent()
    const code = codeText?.replace("Code ", "").split(" ")[0] ?? ""
    expect(code).not.toBe("")

    await joinerPage.goto("/")
    await joinerPage.getByPlaceholder("Join code").fill(code)
    await joinerPage.getByRole("button", { name: "Join" }).click()
    await joinerPage.waitForURL(/\/rooms\/.+/)

    await creatorPage
      .getByRole("button", { name: "Start", exact: true })
      .click()

    // Whichever page holds the current turn gets a clickable legal move --
    // the referee's seed determines who goes first, so poll both pages
    // (locators from different pages can't be combined with .or()) rather
    // than assuming which one it is.
    const moveOn = (page: typeof creatorPage) =>
      page
        .locator("button")
        .filter({ hasText: /Draw|Play|red|yellow|green|blue/i })
        .first()

    const winner = await Promise.race([
      moveOn(creatorPage)
        .waitFor({ state: "visible", timeout: 15_000 })
        .then(() => "creator" as const),
      moveOn(joinerPage)
        .waitFor({ state: "visible", timeout: 15_000 })
        .then(() => "joiner" as const),
    ])
    await moveOn(winner === "creator" ? creatorPage : joinerPage).click()

    // Both pages' live websocket connections should reflect the applied
    // move -- the hand-count badges update for both once the referee
    // advances the turn, proof the push transport (Phase 10) is actually
    // driving the UI, not a one-time snapshot.
    await expect(creatorPage.getByText(/Seat \d: \d+/).first()).toBeVisible()
    await expect(joinerPage.getByText(/Seat \d: \d+/).first()).toBeVisible()

    await creatorContext.close()
    await joinerContext.close()
  })

  test("Two human players can exchange chat, seen live on both pages", async ({
    browser,
  }) => {
    const creatorEmail = randomEmail()
    const creatorPassword = randomPassword()
    const joinerEmail = randomEmail()
    const joinerPassword = randomPassword()
    await createUser({ email: creatorEmail, password: creatorPassword })
    await createUser({ email: joinerEmail, password: joinerPassword })

    const creatorContext = await browser.newContext()
    const joinerContext = await browser.newContext()
    const creatorPage = await creatorContext.newPage()
    const joinerPage = await joinerContext.newPage()

    await logInUser(creatorPage, creatorEmail, creatorPassword)
    await logInUser(joinerPage, joinerEmail, joinerPassword)

    await creatorPage.getByRole("button", { name: "Create room" }).click()
    await creatorPage
      .getByRole("button", { name: "Create", exact: true })
      .click()

    const codeText = await creatorPage.getByText(/^Code /).textContent()
    const code = codeText?.replace("Code ", "").split(" ")[0] ?? ""
    expect(code).not.toBe("")

    await joinerPage.goto("/")
    await joinerPage.getByPlaceholder("Join code").fill(code)
    await joinerPage.getByRole("button", { name: "Join" }).click()
    await joinerPage.waitForURL(/\/rooms\/.+/)

    // Chat works before the game even starts (docs/rooms.md) -- no Start
    // click needed for this test.
    await creatorPage.getByLabel("Chat message").fill("good luck!")
    await creatorPage.getByRole("button", { name: "Send" }).click()

    await expect(joinerPage.getByText("good luck!")).toBeVisible({
      timeout: 10_000,
    })
    // The sender's own page reflects the send too, over the same websocket
    // that delivered it to the joiner -- not a locally-echoed optimistic
    // update.
    await expect(creatorPage.getByText("good luck!")).toBeVisible()

    await joinerPage.getByLabel("Chat message").fill("thanks, you too")
    await joinerPage.getByRole("button", { name: "Send" }).click()
    await expect(creatorPage.getByText("thanks, you too")).toBeVisible({
      timeout: 10_000,
    })

    await creatorContext.close()
    await joinerContext.close()
  })
})
