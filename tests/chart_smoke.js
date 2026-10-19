// Runs static/progress.js with a tiny fake DOM (no browser needed).
// Usage: node chart_smoke.js <dashboard-chart-data.json> <progress.js>
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const real = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const source = fs.readFileSync(process.argv[3], "utf8");

function run(data, withChart) {
  const els = {}, charts = [];
  const make = (extra) => Object.assign({ hidden: false, listeners: {}, innerHTML: "", textContent: "",
    addEventListener(n, f) { this.listeners[n] = f; }, getContext() { return {}; } }, extra);
  els["progress-data"] = make({ textContent: JSON.stringify(data) });
  ["progress-chart", "chart-box", "chart-caption", "chart-fallback"].forEach((id) => els[id] = make());
  els["chart-svg"] = make({ hidden: true });
  els["subject-select"] = make({ value: "overall" });
  class FakeChart { constructor(ctx, cfg) { this.config = cfg; this.data = cfg.data; charts.push(this); } update() { this.updated = true; } }
  const sandbox = { document: { getElementById: (id) => els[id] || null }, JSON, Math, String };
  if (withChart) sandbox.Chart = FakeChart;
  vm.runInNewContext(source, sandbox);
  return { els, charts };
}
const count = (s, re) => (s.match(re) || []).length;

// 1. Chart.js available: unchanged behaviour
let { els, charts } = run(real, true);
const chart = charts[0];
assert.deepStrictEqual(chart.data.labels, real.exams);
assert.deepStrictEqual(chart.data.datasets[0].data, real.overall.map(p => p.percentage));
assert.strictEqual(chart.config.options.scales.y.min, 0);
assert.strictEqual(chart.config.options.scales.y.max, 100);
assert.strictEqual(chart.config.options.scales.y.ticks.callback(40), "40%");
assert.strictEqual(els["chart-svg"].hidden, true, "SVG fallback stays hidden when Chart.js works");
assert.strictEqual(els["chart-fallback"].hidden, false, "stub default; the notice is only un-hidden in the fallback path");
const cb = chart.config.options.plugins.tooltip.callbacks, p0 = real.overall[0];
assert.strictEqual(cb.label({ dataset: chart.data.datasets[0], dataIndex: 0 }), "Overall: " + p0.obtained + " / " + p0.maximum + " marks (" + p0.percentage + "%)");
assert.ok(cb.afterLabel({ dataset: chart.data.datasets[0], dataIndex: 0 }).includes("of " + real.subjectsExpected + " subjects"));
const sub = real.subjects[0];
els["subject-select"].value = sub.id; els["subject-select"].listeners.change();
assert.strictEqual(chart.data.datasets.length, 2);
assert.strictEqual(chart.data.datasets[0].label, sub.name);
assert.deepStrictEqual(chart.data.datasets[0].data, sub.points.map(p => p ? p.percentage : null));
assert.strictEqual(chart.data.datasets[1].label, "Overall");

// 2. Chart.js NOT available: built-in SVG chart takes over
({ els, charts } = run(real, false));
assert.strictEqual(charts.length, 0);
assert.strictEqual(els["chart-box"].hidden, true, "empty canvas area hidden");
assert.strictEqual(els["chart-fallback"].hidden, false, "notice shown");
assert.strictEqual(els["chart-svg"].hidden, false);
let svg = els["chart-svg"].innerHTML;
assert.ok(svg.startsWith("<svg"));
assert.strictEqual(count(svg, /<circle /g), real.overall.length, "one marker per exam");
assert.strictEqual(count(svg, /<polyline /g), 1);
assert.ok(svg.includes(">0%<") && svg.includes(">100%<"), "0-100% axis");
real.exams.forEach(e => assert.ok(svg.includes(">" + e + "<")));
assert.ok(svg.includes("<title>" + real.exams[0] + " - Overall: " + p0.obtained + " / " + p0.maximum + " marks (" + p0.percentage + "%)"));
assert.ok(els["chart-caption"].textContent.startsWith("Showing Overall."));
els["subject-select"].value = sub.id; els["subject-select"].listeners.change();
svg = els["chart-svg"].innerHTML;
assert.strictEqual(count(svg, /<polyline /g), 2, "subject line + dashed overall line");
assert.ok(svg.includes(">" + sub.name + "<"), "legend names the subject");
assert.ok(els["chart-caption"].textContent.startsWith("Showing " + sub.name));

// 3. gaps are gaps, never zero; one exam draws a point and no line
const gap = JSON.parse(JSON.stringify(real));
gap.exams = ["A", "B", "C"];
gap.overall = [1, 2, 3].map(i => ({ obtained: 50, maximum: 100, percentage: 50, subjects: 1 }));
gap.subjects = [{ id: "S1", name: "Maths", points: [{ obtained: 10, maximum: 20, percentage: 50 }, null, { obtained: 18, maximum: 20, percentage: 90 }] }];
({ els } = run(gap, false)); els["subject-select"].value = "S1"; els["subject-select"].listeners.change();
svg = els["chart-svg"].innerHTML;
assert.strictEqual(count(svg, /<circle /g), 2, "no marker for the missing exam");
assert.ok(!svg.includes("(0%)"), "missing result never drawn as 0%");
const one = JSON.parse(JSON.stringify(real));
one.exams = [real.exams[0]]; one.overall = [real.overall[0]]; one.subjects = real.subjects.map(s => ({ id: s.id, name: s.name, points: [s.points[0]] }));
({ els } = run(one, false));
assert.strictEqual(count(els["chart-svg"].innerHTML, /<circle /g), 1);
assert.strictEqual(count(els["chart-svg"].innerHTML, /<polyline /g), 0);

// 4. names from the database cannot inject HTML into the SVG
const evil = JSON.parse(JSON.stringify(real));
evil.exams[0] = '<img src=x onerror=alert(1)>';
evil.subjects[0].name = '"><script>alert(2)</script>';
({ els } = run(evil, false)); els["subject-select"].value = evil.subjects[0].id; els["subject-select"].listeners.change();
svg = els["chart-svg"].innerHTML;
assert.ok(!svg.includes("<img") && !svg.includes("<script"), "all text is escaped");
assert.ok(svg.includes("&lt;img"));
console.log("chart_smoke ok");
