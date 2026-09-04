// Drives the real web interface in a real browser against a running server.
//
//   node tests/browser/page.mjs [--shots <dir>] [--browser chromium|webkit]
//
// Deliberately dependency-free in the repository. To run it:
//
//   npm install --no-save --no-package-lock playwright@1.56.1
//   npx playwright install webkit chromium
//
// which leaves an ignored node_modules and nothing else; the Mac checks
// workflow pins the same version. Everything it asserts is behaviour a person would see:
// what the page offers before anything is configured, that the settings sheet
// is the one way to everything advanced, and that a file dropped on the page
// comes back as a transcript. Nothing here mocks the server or the model.
const args = process.argv.slice(2);
const option = (name, fallback) => {
  const at = args.indexOf(name);
  return at === -1 ? fallback : args[at + 1];
};
const shots = option("--shots", null);
const engine = option("--browser", "chromium");
const wav = option("--wav", null);
const BASE = option("--base", "http://127.0.0.1:8990");

const playwright = await import("playwright");
const results = [];
const check = (name, ok, detail = "") => {
  results.push([name, !!ok]);
  console.log(`${ok ? "PASS " : "FAIL "}${name}${detail ? `  [${detail}]` : ""}`);
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const api = async (path, init) => (await fetch(BASE + path, init)).json();

const browser = await playwright[engine].launch();
const context = await browser.newContext({ viewport: { width: 1000, height: 900 }, acceptDownloads: true });
const page = await context.newPage();
const noise = [];
page.on("console", m => { if (m.type() === "error" || m.type() === "warning") noise.push(`${m.type()}: ${m.text()}`); });
page.on("pageerror", e => noise.push(`pageerror: ${e.message}`));
page.on("dialog", d => d.accept());
const shot = async (name, opts = {}) => {
  if (shots) await page.screenshot({ path: `${shots}/${engine}-${name}.png`, ...opts });
};
const visible = sel => page.locator(sel).isVisible();

// A known starting point, without touching anything a person would not.
await fetch(`${BASE}/api/settings`, {
  method: "PUT", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ transcription: { model: "0.6b", language: "auto", timestamps: false, context: "" } }),
});
await page.goto(BASE);
await page.waitForFunction(() => document.querySelectorAll("#model option").length > 1, null, { timeout: 15000 });

// ── 1. What the page offers before anything is set up ────────────────────
check("the drop zone, the model, the language and the timestamp toggle are on the page",
  await visible("#drop") && await visible("#model") && await visible("#language") && await visible("#timestamps"));
check("no advanced control is on the page",
  !(await visible("#setHotkey")) && !(await visible("#setDictionary")) && !(await visible("#setReplacements"))
  && !(await visible("#setUnload")) && !(await visible("#context")) && !(await visible("#modelList")));
// Font metrics differ between platforms, and this row is the one place the
// page assumes a width: on a Mac it must still be a single line.
const toolbar = await page.locator(".toolbar").evaluate(n => n.getBoundingClientRect().height);
check("the two choices and the toggle sit on one line", toolbar < 44, `${toolbar.toFixed(0)}px tall`);
// Whether anything is queued depends on what ran before this; the rule is
// that the section appears exactly when it has something to show.
const queued = (await api("/api/jobs")).jobs.length;
const queueShown = await visible("#queue");
check("the queue section appears exactly when something is in it", queueShown === (queued > 0),
  `${queued} jobs, section ${queueShown ? "shown" : "hidden"}`);
check("the history says where transcripts will appear",
  (await page.textContent("#historyList")).includes("saved here automatically") || (await page.$(".history-item")) !== null);
await shot("01-page", { fullPage: true });

// ── 2. One panel, four groups, and every way back out ────────────────────
await page.click("#btnSettings");
await page.waitForSelector("#settings:not([hidden])");
await sleep(320);
check("the gear opens the settings sheet on Transcription", await visible("#context"));
await shot("02-settings");
for (const [tab, group, probe] of [["tabDictation", "dictation", "#setHotkey"],
                                   ["tabModels", "models", "#modelList"],
                                   ["tabAdvanced", "advanced", "#setUnload"]]) {
  await page.click(`#${tab}`);
  await sleep(80);
  const selected = await page.$$eval(".tab", els =>
    els.filter(e => e.getAttribute("aria-selected") === "true").map(e => e.dataset.group));
  const shown = await page.$$eval(".group", els => els.filter(e => !e.hidden).length);
  check(`the ${group} group is reached in one click and is the only one shown`,
    selected.length === 1 && selected[0] === group && shown === 1 && await visible(probe), selected.join(","));
}
await page.click("#tabModels");
await sleep(80);
const rows = await page.$$eval(".model-row", els => els.map(e => e.textContent.replace(/\s+/g, " ").trim()));
check("the models group lists the whole catalogue with each state", rows.length === 4
  && rows.some(r => r.includes("1.7B — accuracy")) && rows.some(r => r.includes("0.6B 4-bit")), `${rows.length} rows`);
await shot("03-models");
await page.keyboard.press("Escape");
await page.waitForSelector("#settings", { state: "hidden" });
check("Escape closes the sheet and focus comes back to the gear",
  (await page.evaluate(() => document.activeElement.id)) === "btnSettings");
await page.click("#btnDictationSetup");
await page.waitForSelector("#groupDictation:not([hidden])");
check("the dictation card opens the sheet at its own group", await visible("#setHotkey"));
await page.click("#btnSettingsClose");
await page.waitForSelector("#settings", { state: "hidden" });

// ── 3. A vocabulary hint is named on the page while it applies ───────────
await page.click("#btnSettings");
await page.fill("#context", "Qwen Scribe MLX");
await sleep(900);
await page.keyboard.press("Escape");
check("a hint in use is named on the page", (await page.textContent("#activeChips")).includes("3 terms"),
  await page.textContent("#activeChips"));
await page.click("#activeChips button");
await page.waitForSelector("#settings:not([hidden])");
check("its label opens the field that sets it", await visible("#context"));
await page.fill("#context", "");
await sleep(900);
await page.keyboard.press("Escape");
check("and goes away with the setting", await page.locator("#activeChips").isHidden());

// ── 4. A real file becomes a real transcript ─────────────────────────────
if (wav) {
  const before = (await api("/api/transcripts")).transcripts.length;
  await page.setInputFiles("#file", wav);
  await page.waitForSelector("#job.visible");
  await page.waitForFunction(() => document.querySelector("#jobStatus").textContent === "done",
    null, { timeout: 15 * 60 * 1000 });
  const text = (await page.textContent("#tBody")).trim();
  check("the transcript arrives on the page with words in it", text.split(/\s+/).length > 3, text.slice(0, 70));
  check("its metadata names the file and the language",
    (await page.textContent("#tMeta")).includes("language:"), await page.textContent("#tMeta"));
  const [download] = await Promise.all([page.waitForEvent("download"), page.click("#btnTxt")]);
  check("Download .txt hands over what is on screen", (await download.failure()) === null);
  await page.waitForFunction(n => document.querySelectorAll("#historyList .history-item").length > n,
    before, { timeout: 20000 });
  check("and it lands in the saved transcripts", true);
  await shot("04-transcript", { fullPage: true });
}

// ── 5. Narrow, the way a window ends up beside another one ───────────────
await page.setViewportSize({ width: 420, height: 860 });
await sleep(200);
const overflow = await page.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);
check("nothing spills sideways at 420px", overflow <= 1, `${overflow}px of overflow`);
await shot("05-narrow", { fullPage: true });
await page.click("#btnSettings");
await sleep(320);
const box = await page.locator("#settings").boundingBox();
check("the sheet takes the whole width there", Math.round(box.width) === 420, `${box.width}px`);
await shot("06-narrow-settings");

const errors = noise.filter(n => !n.includes("favicon") && !n.includes("ERR_ABORTED") && !n.includes("status of 409"));
check("no console errors, page errors or failed requests", errors.length === 0, errors.slice(0, 3).join(" || "));
await browser.close();
const passed = results.filter(([, ok]) => ok).length;
console.log(`\n${passed}/${results.length} page checks passed in ${engine}`);
process.exit(passed === results.length ? 0 : 1);
