// Screenshots of every screen with sample data, for review and docs.
// Usage: npm run dev, then node scripts/screens.mjs OUT_DIR
import { chromium } from "playwright";
const out = process.argv[2] ?? "screens";
const base = process.env.TAG_UI_URL ?? "http://localhost:1420";
const browser = await chromium.launch({ channel: "chrome" });
const shot = async (page, name) => {
  await page.waitForTimeout(400);
  const box = await page.locator("main").boundingBox();
  await page.screenshot({ path: `${out}/${name}.png`, clip: { x: 0, y: 0, width: 520, height: Math.ceil(box.height) } });
};
for (const scheme of ["light", "dark"]) {
  const page = await browser.newPage({ viewport: { width: 520, height: 1000 }, deviceScaleFactor: 2, colorScheme: scheme });
  const suffix = scheme === "dark" ? "-dark" : "";
  await page.goto(base);
  await page.getByText("of 4 online").waitFor();
  await shot(page, "home" + suffix);
  if (scheme === "dark") { await page.close(); continue; }
  await page.getByRole("button", { name: "More for Research Tag" }).click();
  await shot(page, "home-menu");
  await page.getByRole("menuitem", { name: "Rename…" }).click();
  await shot(page, "home-rename");
  await page.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("switch", { name: "Start Research Tag" }).click();
  await page.getByText("3 of 4 online").waitFor();
  await shot(page, "home-started");
  await page.getByRole("button", { name: "Settings" }).click();
  await shot(page, "settings");
  await page.getByRole("button", { name: "Back" }).click();
  await page.getByRole("button", { name: "More for Maya's Tag" }).click();
  await page.getByRole("menuitem", { name: "Show logs" }).click();
  await page.getByText("Connected to Slack").waitFor();
  await shot(page, "logs");
  await page.getByRole("button", { name: "Back" }).click();
  await page.getByRole("button", { name: "Add Tag" }).first().click();
  await page.getByText("Copy your sign-in line").waitFor();
  await shot(page, "connect-copy");
  await page.getByRole("button", { name: "Copy sign-in line" }).click();
  await shot(page, "connect-slack");
  await page.getByRole("button", { name: "I clicked Confirm" }).click();
  await page.getByRole("button", { name: "Paste" }).click();
  await shot(page, "connect-code");
  await page.getByRole("button", { name: "Connect" }).click();
  await page.getByText("Which workspace?").waitFor();
  await shot(page, "connect-workspace");
  await page.getByRole("option", { name: "Klovr" }).click();
  await page.getByText("Where should Tag respond?").waitFor();
  await shot(page, "connect-channels");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByText("Create the Slack app now?").waitFor();
  await shot(page, "connect-confirm");
  await page.getByRole("button", { name: "Yes" }).click();
  await page.getByText("What should Tag be called").waitFor();
  await shot(page, "connect-text");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByText("Which one is you?").waitFor();
  await shot(page, "connect-people");
  await page.getByRole("button", { name: /Jamie Chen/ }).click();
  await page.getByText("Your Tag is ready").waitFor();
  await shot(page, "connect-done");
  // First run: install.
  await page.goto(base + "/?installed=0");
  await page.getByText("Install Tag").first().waitFor();
  await shot(page, "welcome");
  await page.getByRole("button", { name: "Install" }).click();
  await page.waitForTimeout(6000);
  await shot(page, "installing");
  await page.getByText("Tag is installed").waitFor({ timeout: 20000 });
  await shot(page, "installed");
  await page.close();
}
await browser.close();
console.log("Saved screenshots to", out);
