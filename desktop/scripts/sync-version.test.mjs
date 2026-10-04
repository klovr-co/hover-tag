import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, cpSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

for (const release of ["", "v0.3.0-alpha.7", "invalid"]) {
  test(`version source: ${release || "VERSION"}`, () => {
    const root = mkdtempSync(join(tmpdir(), "tag-version-"));
    try {
      mkdirSync(join(root, "desktop/scripts"), { recursive: true });
      mkdirSync(join(root, "desktop/src-tauri"));
      cpSync(new URL("sync-version.mjs", import.meta.url), join(root, "desktop/scripts/sync-version.mjs"));
      writeFileSync(join(root, "VERSION"), "0.3.0-alpha\n");
      writeFileSync(join(root, "desktop/package.json"), '{"version":"0.2.0"}\n');
      writeFileSync(join(root, "desktop/package-lock.json"), '{"version":"0.2.0","packages":{"":{"version":"0.2.0"}}}\n');
      writeFileSync(join(root, "desktop/src-tauri/Cargo.toml"), '[package]\nname = "tag-desktop"\nversion = "0.2.0"\n');
      writeFileSync(join(root, "desktop/src-tauri/Cargo.lock"), '[[package]]\nname = "tag-desktop"\nversion = "0.2.0"\n');
      const run = () => spawnSync(process.execPath, [join(root, "desktop/scripts/sync-version.mjs")], { env: { ...process.env, RELEASE_TAG: release } });
      assert.equal(run().status, release === "invalid" ? 1 : 0);
      const expected = release === "invalid" ? "0.2.0" : release.replace(/^v/, "") || "0.3.0-alpha";
      for (const file of ["package.json", "package-lock.json"]) {
        const data = JSON.parse(readFileSync(join(root, "desktop", file), "utf8"));
        assert.equal(data.version, expected);
        if (data.packages) assert.equal(data.packages[""].version, expected);
      }
      for (const file of ["Cargo.toml", "Cargo.lock"]) {
        assert.ok(readFileSync(join(root, "desktop/src-tauri", file), "utf8").includes(`version = "${expected}"`));
      }
      const before = readFileSync(join(root, "desktop/package.json"), "utf8");
      run();
      assert.equal(readFileSync(join(root, "desktop/package.json"), "utf8"), before);
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
}
