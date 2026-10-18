// Runs static/insights.js with a fake DOM and a fake Chart class (no browser needed).
// Usage: node insights_smoke.js <insights-chart-data.json> <insights.js>
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
// arrays created inside vm.runInNewContext have another Array prototype, so compare through JSON
const same = (a, b, msg) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b), msg);
const source = fs.readFileSync(process.argv[3], "utf8");

function run(payload, withChart, ids) {
  const els = {}, charts = [];
  const make = (extra) => Object.assign({ hidden: false, listeners: {}, textContent: "", value: "",
    addEventListener(n, f) { this.listeners[n] = f; }, getContext() { return {}; }, parentNode: { hidden: false } }, extra);
  els["insights-data"] = make({ textContent: JSON.stringify(payload) });
  els["insights-fallback"] = make({ hidden: true });
  els["marks-caption"] = make(); els["exam-select"] = make({ value: payload.bars ? payload.bars.default : "" });
  (ids || ["attendance-pie", "marks-bar", "monthly-trend"]).forEach((id) => els[id] = make());
  class FakeChart { constructor(ctx, cfg) { this.config = cfg; this.data = cfg.data; this.options = cfg.options; charts.push(this); } update() { this.updated = true; } }
  const sandbox = { document: { getElementById: (id) => els[id] || null }, JSON, Math };
  if (withChart) sandbox.Chart = FakeChart;
  vm.runInNewContext(source, sandbox);
  return { els, charts };
}

// ---- 1. all three charts ----
let { els, charts } = run(data, true);
assert.strictEqual(charts.length, data.monthly ? 3 : 2);
const [pieChart, barChart, lineChart] = charts;

// doughnut: teal Present, coral Absent, counts and percentages exactly as sent by Python
const pie = data.pie, pc = pieChart.config;
assert.strictEqual(pc.type, "doughnut");
same(pc.data.datasets[0].data, [pie.present, pie.absent]);
same(pc.data.datasets[0].backgroundColor, ["#0D9488", "#F97373"]);
assert.ok(pc.data.labels[0].includes(pie.present + " (" + pie.presentPct + "%)"), "legend shows count and percentage");
assert.ok(pc.data.labels[1].includes(pie.absent + " (" + pie.absentPct + "%)"));
const pcb = pc.options.plugins.tooltip.callbacks;
assert.strictEqual(pcb.title([{ dataIndex: 0 }]), "Present");
assert.strictEqual(pcb.label({ dataIndex: 0 }), pie.present + " of " + pie.total + " classes (" + pie.presentPct + "%)");
assert.strictEqual(pcb.label({ dataIndex: 1 }), pie.absent + " of " + pie.total + " classes (" + pie.absentPct + "%)");
const drawn = [];
pieChart.config.plugins[0].afterDraw({ chartArea: { left: 0, right: 200, top: 0, bottom: 200 },
  ctx: { save() {}, restore() {}, fillText(t) { drawn.push(t); } } });
assert.ok(drawn.includes(pie.percentage + "%"), "attendance percentage written in the centre");

// bars: obtained (violet) vs maximum (navy tint), exam selector, tooltips
const bars = data.bars, bc = barChart.config, exam = bars.default, rows = bars.byExam[exam].rows;
assert.strictEqual(bc.type, "bar");
same(bc.data.labels, rows.map(r => r.subject));
same(bc.data.datasets[0].data, rows.map(r => r.obtained));
same(bc.data.datasets[1].data, rows.map(r => r.maximum));
assert.strictEqual(bc.data.datasets[0].backgroundColor, "#7C3AED");
assert.strictEqual(bc.data.datasets[0].hoverBackgroundColor, "#0D9488");
assert.strictEqual(bc.options.scales.y.max, bars.byExam[exam].axisMax);
const bcb = bc.options.plugins.tooltip.callbacks, r0 = rows[0];
assert.strictEqual(bcb.title([{ label: r0.subject }]), r0.subject);
assert.strictEqual(bcb.label({ datasetIndex: 0, dataIndex: 0, dataset: bc.data.datasets[0] }), "Marks obtained: " + r0.obtained + " / " + r0.maximum + " (" + r0.percentage + "%)");
assert.strictEqual(bcb.label({ datasetIndex: 1, dataIndex: 0, dataset: bc.data.datasets[1] }), "Maximum marks: " + r0.maximum);
const other = bars.exams.filter(e => e !== exam)[0];
if (other) {
  els["exam-select"].value = other; els["exam-select"].listeners.change();
  same(barChart.data.datasets[0].data, bars.byExam[other].rows.map(r => r.obtained));
  assert.strictEqual(barChart.options.scales.y.max, bars.byExam[other].axisMax);
  assert.strictEqual(els["marks-caption"].textContent, bars.byExam[other].caption);
  assert.ok(barChart.updated);
}

// line (present only when the data has at least two months)
if (data.monthly) {
  const m = data.monthly, lc = lineChart.config;
  assert.strictEqual(lc.type, "line");
  same(lc.data.labels, m.labels);
  same(lc.data.datasets[0].data, m.percentages);
  assert.strictEqual(lc.options.scales.y.min, 0); assert.strictEqual(lc.options.scales.y.max, 100);
  const colours = lc.data.datasets[0].pointBackgroundColor;
  m.labels.forEach((l, i) => assert.strictEqual(colours[i], m.highest.includes(l) ? "#7C3AED" : (m.lowest.includes(l) ? "#F97373" : "#0D9488")));
  const lcb = lc.options.plugins.tooltip.callbacks;
  assert.strictEqual(lcb.title([{ label: m.labels[0] }]), m.labels[0]);
  assert.strictEqual(lcb.label({ parsed: { y: m.percentages[0] } }), "Attendance: " + m.percentages[0] + "%");
  assert.strictEqual(lcb.afterLabel({ dataIndex: 0 }), m.present[0] + " present out of " + m.totals[0] + " valid records");
}

// ---- 2. Chart.js missing: canvases hidden, notice shown, nothing throws ----
({ els, charts } = run(data, false));
assert.strictEqual(charts.length, 0);
["attendance-pie", "marks-bar", "monthly-trend"].forEach(id => { if (data.monthly || id !== "monthly-trend") assert.strictEqual(els[id].parentNode.hidden, true, id); });
assert.strictEqual(els["insights-fallback"].hidden, false);

// ---- 3. nothing to chart: no canvases on the page, no errors, no charts ----
({ els, charts } = run({ pie: null, bars: null, monthly: null }, true, []));
assert.strictEqual(charts.length, 0);
({ els, charts } = run({ pie: null, bars: null, monthly: null }, false, []));
assert.strictEqual(els["insights-fallback"].hidden, true, "no notice when there is no chart to replace");
console.log("insights_smoke ok");
