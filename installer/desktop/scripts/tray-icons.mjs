// Renders the menu bar / tray waterdrop: outline when no Tag is online, filled otherwise.
// macOS uses them as template images (black + alpha); other platforms get a white copy.
import { chromium } from "playwright";
const drop = (fill, color) => `<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 22 22">
  <path d="M11 2.5C11 2.5 4.8 9.6 4.8 13.6a6.2 6.2 0 0 0 12.4 0C17.2 9.6 11 2.5 11 2.5z"
    fill="${fill ? color : "none"}" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 44, height: 44 } });
for (const [name, fill, color] of [["tray", false, "#000"], ["tray-online", true, "#000"],
  ["tray-light", false, "#fff"], ["tray-light-online", true, "#fff"]]) {
  await page.setContent(`<body style="margin:0;background:transparent">${drop(fill, color)}</body>`);
  await page.screenshot({ path: `src-tauri/icons/${name}.png`, omitBackground: true });
}
await browser.close();
