// Screenshots of every screen with sample data, for review and docs.
// Usage: npm run dev, then node scripts/screens.mjs OUT_DIR
import { chromium } from "playwright";
const out = process.argv[2] ?? "screens";
const base = process.env.TAG_UI_URL ?? "http://localhost:1420";
const browser = await chromium.launch({ channel: "chrome" });
let width = 520;
const shot = async (page, name) => {
  await page.waitForTimeout(600);
  const box = await page.locator("main").boundingBox();
  await page.screenshot({ path: `${out}/${name}.png`, clip: { x: 0, y: 0, width, height: Math.ceil(box.height) } });
};
const button = (page, name) => page.getByRole("button", { name }).first();
for (const scheme of ["light", "dark"]) {
  const page = await browser.newPage({ viewport: { width: 800, height: 1100 }, deviceScaleFactor: 2, colorScheme: scheme });
  const suffix = scheme === "dark" ? "-dark" : "";
  // Home, with every Tag, then with one and none.
  await page.goto(base + "/?update=1");
  await page.getByText("of 3 online").waitFor();
  await shot(page, "home" + suffix);
  await page.goto(base + "/?tags=1");
  await page.getByText("Try it in Slack").waitFor();
  await shot(page, "home-one" + suffix);
  await page.goto(base + "/?tags=0");
  await page.getByText("Bring your first Tag to Slack").waitFor();
  await shot(page, "home-empty" + suffix);
  // Tag detail is the wide screen.
  await page.goto(base);
  await button(page, "Open Maya's Tag").click();
  await page.getByText("Replied in").waitFor();
  width = 800;
  await shot(page, "tag-activity" + suffix);
  await page.getByRole("tab", { name: "Details" }).click();
  await page.getByRole("radiogroup", { name: "Thinking level" }).waitFor({ timeout: 10000 });
  await shot(page, "tag-details" + suffix);
  width = 520;
  // Settings and AI & models.
  await button(page, "Back to Your Tags").click();
  await button(page, "Settings").click();
  await page.getByText("Release channel").waitFor();
  await shot(page, "settings" + suffix);
  await button(page, "Open AI and models").click();
  await page.getByRole("button", { name: "Check connections" }).waitFor();
  await page.waitForTimeout(1500);
  await shot(page, "ai" + suffix);
  // Add a Tag, step by step, with the sample setup conversation.
  await page.goto(base);
  await button(page, "Add Tag").click();
  await page.getByText("Meet your new Tag").waitFor();
  await shot(page, "add-meet" + suffix);
  await button(page, /Shuffle picture/).click();
  await page.waitForTimeout(800);
  for (let i = 0; i < 3 && !(await page.getByText("Which workspace?").isVisible()); i++) {
    if (await page.getByText("Choose Maya's Tag's model").isVisible()) await shot(page, "add-ai" + suffix);
    await button(page, /Continue/).click();
    await page.waitForTimeout(1500);
  }
  await page.getByText("Which workspace?").waitFor();
  await shot(page, "add-workspace" + suffix);
  await page.getByRole("option", { name: /Klovr/ }).click();
  await page.getByText("Ready to create it in Klovr?").waitFor();
  await shot(page, "add-create" + suffix);
  await button(page, "Create in Slack").click();
  await page.getByText("Where should Maya's Tag start?").waitFor({ timeout: 15000 });
  await shot(page, "add-channels" + suffix);
  await button(page, /Continue with/).click();
  await page.getByText("Say hi to Maya's Tag").waitFor({ timeout: 15000 });
  await shot(page, "add-ready" + suffix);
  // First run: install.
  await page.goto(base + "/?installed=0");
  await page.getByText("Your personal assistant, in Slack.").waitFor();
  await shot(page, "welcome" + suffix);
  await button(page, "Install Tag").click();
  await page.waitForTimeout(6000);
  await shot(page, "installing" + suffix);
  await page.getByText("Tag is installed").waitFor({ timeout: 20000 });
  await shot(page, "installed" + suffix);
  await page.close();
}
await browser.close();
console.log("Saved screenshots to", out);
