// Copies MapLibre's self-contained worker bundle into public/ so it is served from this origin.
// MapLibre normally resolves the worker relative to its own module URL, which bundler chunking breaks.
// Runs before every build (npm "prebuild"); the copied file is gitignored and versioned by path.
import { copyFileSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pkgDir = join(root, "node_modules", "maplibre-gl");
const { version } = JSON.parse(readFileSync(join(pkgDir, "package.json"), "utf8"));
const outDir = join(root, "public", "vendor", "maplibre-gl");
rmSync(outDir, { recursive: true, force: true });
mkdirSync(join(outDir, version), { recursive: true });
copyFileSync(join(pkgDir, "dist", "maplibre-gl-worker.mjs"), join(outDir, version, "maplibre-gl-worker.mjs"));
console.log(`maplibre-gl worker ${version} -> public/vendor/maplibre-gl/${version}/`);
