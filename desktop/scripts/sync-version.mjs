// VERSION is the product version; CI supplies the immutable release tag.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
const root = new URL("../../", import.meta.url);
const version = (process.env.RELEASE_TAG || readFileSync(new URL("VERSION", root), "utf8")).trim().replace(/^v/, "");
if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(version)) throw new Error(`Invalid Tag version: ${version}`);
for (const name of ["desktop/package.json", "desktop/package-lock.json"]) {
  const path = new URL(name, root);
  const data = JSON.parse(readFileSync(path, "utf8"));
  data.version = version;
  if (data.packages?.[""]) data.packages[""].version = version;
  writeFileSync(path, JSON.stringify(data, null, 2) + "\n");
}
const cargo = new URL("desktop/src-tauri/Cargo.toml", root);
writeFileSync(cargo, readFileSync(cargo, "utf8").replace(/^(version = ")[^"]+/m, `$1${version}`));
const lock = new URL("desktop/src-tauri/Cargo.lock", root);
writeFileSync(lock, readFileSync(lock, "utf8").replace(/(name = "tag-desktop"\nversion = ")[^"]+/, `$1${version}`));
console.log(`Tag ${version} (${fileURLToPath(new URL("VERSION", root))})`);
