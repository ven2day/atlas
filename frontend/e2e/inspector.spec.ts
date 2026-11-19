// §6.7: the inspector is complete for an executed and for a vetoed decision - the plain-language
// summary first, then the technical lineage (advanced sections are collapsed and opened here).

import { expect, test } from "@playwright/test";

import { decisionId, open } from "./helpers";

test("an executed decision: summary, then signal → risk → order → fill → exit → counterfactual", async ({ page }) => {
  await open(page);
  await page.goto(`/decisions/${await decisionId(page, "INFY", "A", "submitted")}`);

  // The trader-facing layer: the plain-language journey, derived from the same recorded events.
  await expect(page.getByRole("heading", { name: "What happened" })).toBeVisible();
  const journey = page.getByRole("list", { name: "Decision journey" });
  await expect(journey).toBeVisible();
  for (const stage of ["Signal", "AI review", "Risk check", "Order", "Position / Exit"]) {
    await expect(journey.getByText(stage, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByRole("list", { name: "Outcome by book" })).toContainText("Book A");
  await expect(page.getByText("What to review or do next").first()).toBeVisible();

  // The technical timeline underneath keeps every recorded detail.
  const lineage = page.getByRole("region").or(page.locator("section")).filter({ hasText: "Lineage" });
  await expect(page.getByRole("heading", { name: /INFY · momentum/ })).toBeVisible();
  for (const text of ["Market context", "Decision price", "Arrival price", "rsi_14", "agreement"]) {
    await expect(lineage.getByText(text).first()).toBeVisible();
  }
  await expect(page.getByText("Book A").first()).toBeVisible();
  await expect(page.getByText("APPROVED").first()).toBeVisible(); // the risk decision
  await expect(page.getByText("FILLED").first()).toBeVisible(); // the order
  await expect(page.getByText("Fill time")).toBeVisible(); // the fills
  await expect(page.getByText("TARGET").first()).toBeVisible(); // the exit
  await expect(page.getByText(/\+₹5,598\.71/).first()).toBeVisible(); // its net P&L

  // The counterfactual is advanced diagnostics: collapsed until asked for.
  await expect(page.getByText("Alpha vs NIFTY")).toBeHidden();
  await page.getByRole("button", { name: /Shadow ledger detail/ }).click();
  await expect(page.getByText("Alpha vs NIFTY")).toBeVisible(); // the counterfactual
});

test("a vetoed decision: the advisor, its evidence, and what the veto avoided", async ({ page }) => {
  await open(page);
  await page.goto(`/decisions/${await decisionId(page, "TCS", "B", "vetoed")}`);
  await expect(page.getByRole("heading", { name: /TCS · momentum/ })).toBeVisible();

  // The summary says a veto is the AI's decision, and risk was never asked for that book.
  const outcomes = page.getByRole("list", { name: "Outcome by book" });
  await expect(outcomes).toContainText(/Book B: AI veto/);
  await expect(outcomes).toContainText(/risk checks were never asked/i);

  await expect(page.getByText("B VETO").first()).toBeVisible(); // B's disposition
  // The veto claim appears twice by design: once in the plain summary, once in the advisor detail.
  await expect(page.getByText("scripted demo veto (no model runs in the demo)").first()).toBeVisible();
  const chip = page.getByRole("button", { name: "signal.instrument_key" });
  await expect(chip).toBeVisible(); // the evidence reference
  await expect(page.getByText("llm_role_veto_disabled")).toBeVisible(); // C abstained, and why
  await expect(page.getByText("STOP").first()).toBeVisible(); // A and C were stopped out

  // What the veto avoided lives in the collapsed shadow-ledger detail.
  await page.getByRole("button", { name: /Shadow ledger detail/ }).click();
  await expect(page.getByText(/−₹3,825\.41/).first()).toBeVisible(); // the loss B avoided
});

test("an unknown decision says so", async ({ page }) => {
  await open(page, "/decisions/0123456789abcdef");
  await expect(page.getByText("No decision recorded with id 0123456789abcdef.")).toBeVisible();
});
