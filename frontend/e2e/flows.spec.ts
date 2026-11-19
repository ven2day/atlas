// §6.7: auth is required; HALT / RESUME typed confirmations work end to end; the shortcuts
// toggle works (off by default); the palette opens a decision by id.

import { expect, test } from "@playwright/test";

import { api, decisionId, open } from "./helpers";

test("without the token there is no data, only the way in", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Not authorised" })).toBeVisible();
  await expect(page.getByText("Books")).toHaveCount(0);
  expect((await request.get("/api/summary")).status()).toBe(401);
  expect((await request.get("/api/summary", { headers: { Authorization: "Bearer wrong" } })).status()).toBe(401);
});

test("HALT, then RESUME with the typed phrase", async ({ page }) => {
  await open(page);
  await page.getByRole("banner").getByRole("button", { name: "HALT" }).click();
  const halt = page.getByRole("dialog", { name: "Halt all books" });
  await halt.getByLabel("Reason (recorded with the action)").fill("e2e: checking the halt flow");
  await halt.getByRole("button", { name: "Halt" }).click();
  await expect(page.getByRole("banner").getByText("A HALT")).toBeVisible();

  await page.goto("/risk");
  await expect(page.getByText("HALTED").first()).toBeVisible();
  await page.getByRole("button", { name: "RESUME A" }).click();
  const resume = page.getByRole("dialog", { name: "Resume book A" });
  const confirm = resume.getByRole("button", { name: "Resume" });
  await resume.getByLabel("Reason (recorded with the action)").fill("e2e: done");
  await resume.getByLabel(/Type RESUME to confirm/).fill("resume");
  await expect(confirm).toBeDisabled(); // the phrase is exact
  await resume.getByLabel(/Type RESUME to confirm/).fill("RESUME");
  await confirm.click();
  await expect(resume).toBeHidden();
  const risk = await api<{ books: { kill_switches: { scope: string; state: string; actor: string }[] }[] }>(page, "/api/risk?book=A");
  expect(risk.books[0]!.kill_switches.find((k) => k.scope === "global")).toMatchObject({ state: "ARMED", actor: "web" });

  for (const book of ["B", "C"]) {
    // leave the demo as it was
    await page.request.post("/api/risk/resume", {
      headers: { Authorization: `Bearer ${process.env.RQ_TOKEN}`, Origin: new URL(page.url()).origin },
      data: { confirm: true, phrase: "RESUME", reason: "e2e: restore", book },
    });
  }
});

test("single-key shortcuts are off until turned on", async ({ page }) => {
  await open(page);
  await page.keyboard.press("2");
  await expect(page).toHaveURL(/\/$/); // nothing happened
  await page.getByRole("button", { name: /keys: off/ }).click();
  await page.keyboard.press("2");
  await expect(page).toHaveURL(/\/decisions$/);
  await page.getByRole("button", { name: /keys: on/ }).click(); // back to the default
});

test("the palette opens a decision by id", async ({ page }) => {
  await open(page);
  const id = await decisionId(page, "INFY", "A", "submitted");
  await page.keyboard.press("Control+k");
  await page.getByPlaceholder(/Go to a screen/).fill(id);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/decisions/${id}$`));
});

test("the command center answers 'what is happening right now' and offers session control", async ({ page }) => {
  await open(page);
  // Global setup waits for the demo session to reach EXIT, so by test time the engine is idle:
  // the panel must say so honestly and offer both ways to start a new session.
  await expect(page.getByText("Right now").first()).toBeVisible();
  await expect(page.getByText("IDLE").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Start paper session" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start demo replay" })).toBeVisible();
  await expect(page.getByText(/No session is running/).first()).toBeVisible();
  // Data freshness always carries a plain word, never just a number.
  await expect(page.getByText(/Data (LIVE|DELAYED|STALE|NO DATA)/).first()).toBeVisible();
});

test("the plain-language trading guide is reachable for a new trader", async ({ page }) => {
  await open(page);
  const guide = page.getByRole("button", { name: /How RakshaQuant trades/ });
  await expect(guide).toBeVisible();
  await guide.click();
  await expect(page.getByText("Market data arrives").first()).toBeVisible();
  await expect(page.getByText(/Book A - no AI/).first()).toBeVisible();
  await expect(page.getByText(/can only veto/i).first()).toBeVisible();
});
