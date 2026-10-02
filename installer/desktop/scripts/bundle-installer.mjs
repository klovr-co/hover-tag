// Copies the installer from this commit into the app, so Tag.app installs
// with reviewed code instead of whatever is on main at install time.
import { cpSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const repo = join(dirname(fileURLToPath(import.meta.url)), "../../..");
const out = join(repo, "installer/desktop/src-tauri/resources/installer");
rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "scripts"), { recursive: true });
for (const file of ["install.sh", "install.ps1", "release-channels.json", "scripts/tag_install.py"]) {
  cpSync(join(repo, file), join(out, file));
}
console.log("Bundled installer into", out);
