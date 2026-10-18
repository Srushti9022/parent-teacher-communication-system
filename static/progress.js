/* Draws "My Child's Academic Progress". The figures come from the JSON block in the page,
   which the server builds from the child's current marks on every page load.
   Chart.js (from a CDN) draws the interactive chart. If it cannot load, a simple built-in SVG
   chart with the same data, axes, tooltips and subject selector is drawn instead. */
(function () {
  "use strict";
  var dataEl = document.getElementById("progress-data");
  var canvas = document.getElementById("progress-chart");
  var select = document.getElementById("subject-select");
  var caption = document.getElementById("chart-caption");
  var fallback = document.getElementById("chart-fallback");
  var svgHost = document.getElementById("chart-svg");
  var box = document.getElementById("chart-box") || canvas;
  if (!dataEl || !canvas || !select) { return; }

  var data = JSON.parse(dataEl.textContent);
  var PURPLE = "#5b3fa0";
  var TEAL = "#0f766e";

  function percentages(points) {
    return points.map(function (p) { return p ? p.percentage : null; });   // null = no result, never 0
  }

  /* The lines to draw: the chosen subject (or Overall), plus a dashed Overall line behind a subject. */
  function seriesFor(key) {
    var overall = { label: "Overall", points: data.overall, isOverall: true, color: PURPLE, dashed: false };
    if (key === "overall") { return [overall]; }
    var sub = data.subjects.filter(function (s) { return s.id === key; })[0];
    if (!sub) { return [overall]; }
    overall.dashed = true;
    return [{ label: sub.name, points: sub.points, isOverall: false, color: TEAL, dashed: false }, overall];
  }

  function describe(key) {
    var s = seriesFor(key)[0];
    var parts = data.exams.map(function (exam, i) {
      var p = s.points[i];
      return exam + ": " + (p ? p.percentage + "%" : "not recorded");
    });
    return "Showing " + s.label + ". " + parts.join("; ") + ".";
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---------- built-in fallback: returns an SVG string (all text escaped) ---------- */
  function svgChart(key) {
    var W = 640, H = 340, L = 52, R = 24, T = 16, B = 76, inset = 44;
    var pw = W - L - R, ph = H - T - B, n = data.exams.length;
    var r = function (v) { return Math.round(v * 10) / 10; };
    var x = function (i) { return r(n === 1 ? L + pw / 2 : L + inset + (pw - 2 * inset) * i / (n - 1)); };
    var y = function (v) { return r(T + ph * (1 - v / 100)); };
    var out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" ' +
      'aria-label="Line chart of percentage in each exam, from 0 to 100 percent" ' +
      'font-family="Segoe UI, system-ui, sans-serif" font-size="12">'];
    for (var g = 0; g <= 100; g += 20) {
      out.push('<line x1="' + L + '" y1="' + y(g) + '" x2="' + (W - R) + '" y2="' + y(g) + '" stroke="#e6defa"/>');
      out.push('<text x="' + (L - 8) + '" y="' + (y(g) + 4) + '" text-anchor="end" fill="#5f5878">' + g + '%</text>');
    }
    out.push('<text transform="rotate(-90)" x="' + (-r(T + ph / 2)) + '" y="14" text-anchor="middle" fill="#5f5878">Percentage</text>');
    data.exams.forEach(function (exam, i) {
      out.push('<text x="' + x(i) + '" y="' + (T + ph + 20) + '" text-anchor="middle" fill="#2a2340">' + escapeHtml(exam) + '</text>');
    });
    out.push('<text x="' + r(L + pw / 2) + '" y="' + (T + ph + 38) + '" text-anchor="middle" fill="#5f5878">Exam (earliest to latest)</text>');

    var series = seriesFor(key);
    series.slice().reverse().forEach(function (s) {        // draw Overall first so the subject sits on top
      var run = [];
      var flush = function () {                            // a missing result breaks the line, it is never drawn as 0
        if (run.length > 1) {
          out.push('<polyline fill="none" stroke="' + s.color + '" stroke-width="' + (s.dashed ? 2 : 3) + '"' +
            (s.dashed ? ' stroke-dasharray="6 4"' : '') + ' points="' + run.join(" ") + '"/>');
        }
        run = [];
      };
      s.points.forEach(function (p, i) {
        if (!p) { flush(); return; }
        run.push(x(i) + "," + y(p.percentage));
      });
      flush();
      s.points.forEach(function (p, i) {
        if (!p) { return; }
        var tip = data.exams[i] + " - " + s.label + ": " + p.obtained + " / " + p.maximum + " marks (" + p.percentage + "%)";
        if (s.isOverall) { tip += ", " + p.subjects + " of " + data.subjectsExpected + " subjects counted"; }
        var cx = x(i), cy = y(p.percentage);
        var shape = s.dashed
          ? '<polygon points="' + cx + ',' + (cy - 6) + ' ' + (cx - 6) + ',' + (cy + 5) + ' ' + (cx + 6) + ',' + (cy + 5) + '" fill="' + s.color + '" stroke="#fff" stroke-width="1.5">'
          : '<circle cx="' + cx + '" cy="' + cy + '" r="5" fill="' + s.color + '" stroke="#fff" stroke-width="1.5">';
        out.push(shape + '<title>' + escapeHtml(tip) + '</title>' + (s.dashed ? '</polygon>' : '</circle>'));
      });
    });
    series.forEach(function (s, k) {                       // legend
      var lx = L + k * 170;
      out.push('<line x1="' + lx + '" y1="' + (H - 12) + '" x2="' + (lx + 26) + '" y2="' + (H - 12) + '" stroke="' + s.color + '" stroke-width="3"' +
        (s.dashed ? ' stroke-dasharray="6 4"' : '') + '/>');
      out.push('<text x="' + (lx + 32) + '" y="' + (H - 8) + '" fill="#2a2340">' + escapeHtml(s.label) + '</text>');
    });
    out.push("</svg>");
    return out.join("");
  }

  if (typeof Chart === "undefined") {                      // CDN blocked or offline
    if (box) { box.hidden = true; }
    if (fallback) { fallback.hidden = false; }
    if (svgHost) {
      svgHost.hidden = false;
      var drawSvg = function () {
        svgHost.innerHTML = svgChart(select.value);
        if (caption) { caption.textContent = describe(select.value); }
      };
      select.addEventListener("change", drawSvg);
      drawSvg();
    }
    return;
  }

  /* ---------- Chart.js ---------- */
  function datasetsFor(key) {
    return seriesFor(key).map(function (s) {
      return {
        label: s.label, points: s.points, isOverall: s.isOverall, data: percentages(s.points),
        borderColor: s.color, backgroundColor: s.color, borderWidth: s.dashed ? 2 : 3, borderDash: s.dashed ? [6, 4] : [],
        pointStyle: s.dashed ? "triangle" : "circle", pointRadius: 5, pointHoverRadius: 7, tension: 0.2, spanGaps: false
      };
    });
  }

  var chart = new Chart(canvas.getContext("2d"), {
    type: "line",
    data: { labels: data.exams, datasets: datasetsFor("overall") },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      scales: {
        y: {
          min: 0, max: 100,
          ticks: { stepSize: 20, callback: function (v) { return v + "%"; } },
          title: { display: true, text: "Percentage" },
          grid: { color: "#e6defa" }
        },
        x: { title: { display: true, text: "Exam (earliest to latest)" }, grid: { display: false } }
      },
      plugins: {
        legend: { display: true, position: "bottom" },
        tooltip: {
          callbacks: {
            title: function (items) { return items[0].label; },
            label: function (ctx) {
              var p = ctx.dataset.points[ctx.dataIndex];
              if (!p) { return ctx.dataset.label + ": no result recorded"; }
              return ctx.dataset.label + ": " + p.obtained + " / " + p.maximum + " marks (" + p.percentage + "%)";
            },
            afterLabel: function (ctx) {
              var p = ctx.dataset.points[ctx.dataIndex];
              if (p && ctx.dataset.isOverall) { return p.subjects + " of " + data.subjectsExpected + " subjects counted"; }
              return "";
            }
          }
        }
      }
    }
  });

  function show(key) {
    chart.data.datasets = datasetsFor(key);
    chart.update();
    if (caption) { caption.textContent = describe(key); }
  }

  select.addEventListener("change", function () { show(select.value); });
  if (caption) { caption.textContent = describe("overall"); }
}());
