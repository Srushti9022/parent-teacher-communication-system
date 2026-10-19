/* Draws the three dashboard charts: attendance doughnut, marks-by-subject bars, monthly attendance line.
   Every figure (counts, percentages, captions, highest/lowest month, axis maximum) is calculated in Python
   and arrives in the JSON block; this file only draws it. */
(function () {
  "use strict";
  var byId = function (id) { return document.getElementById(id); };
  var dataEl = byId("insights-data");
  if (!dataEl) { return; }
  var data = JSON.parse(dataEl.textContent);

  var C = { navy: "#172554", teal: "#0D9488", violet: "#7C3AED", coral: "#F97373", grid: "#E2E8F0", text: "#334155" };
  var pieCanvas = byId("attendance-pie"), barCanvas = byId("marks-bar"), lineCanvas = byId("monthly-trend");
  var canvases = [pieCanvas, barCanvas, lineCanvas].filter(Boolean);

  if (typeof Chart === "undefined") {            // CDN blocked or offline: the text and tables on the page remain
    canvases.forEach(function (c) { (c.parentNode || c).hidden = true; });
    var notice = byId("insights-fallback");
    if (notice && canvases.length) { notice.hidden = false; }
    return;
  }

  var reduce = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var animation = { duration: reduce ? 0 : 700, easing: "easeOutQuart" };
  var legend = { position: "bottom", labels: { usePointStyle: true, padding: 16, color: C.text } };
  var axisTitle = function (text) { return { display: true, text: text, color: C.text }; };

  /* ---- 1. attendance doughnut ---- */
  if (pieCanvas && data.pie) {
    var pie = data.pie;
    new Chart(pieCanvas.getContext("2d"), {
      type: "doughnut",
      data: {
        labels: ["Present: " + pie.present + " (" + pie.presentPct + "%)", "Absent: " + pie.absent + " (" + pie.absentPct + "%)"],
        datasets: [{ data: [pie.present, pie.absent], backgroundColor: [C.teal, C.coral], borderColor: "#ffffff",
                     borderWidth: 3, hoverOffset: 10 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "68%", animation: animation,
        plugins: {
          legend: legend,
          tooltip: {
            callbacks: {
              title: function (items) { return items[0].dataIndex === 0 ? "Present" : "Absent"; },
              label: function (ctx) {
                var present = ctx.dataIndex === 0;
                return (present ? pie.present : pie.absent) + " of " + pie.total + " classes (" + (present ? pie.presentPct : pie.absentPct) + "%)";
              }
            }
          }
        }
      },
      plugins: [{                                    // writes the attendance percentage (from Python) in the middle
        id: "centerText",
        afterDraw: function (chart) {
          var a = chart.chartArea, ctx = chart.ctx, cx = (a.left + a.right) / 2, cy = (a.top + a.bottom) / 2;
          ctx.save();
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillStyle = C.navy; ctx.font = "700 26px 'Segoe UI', system-ui, sans-serif";
          ctx.fillText(pie.percentage + "%", cx, cy - 8);
          ctx.fillStyle = "#475569"; ctx.font = "12px 'Segoe UI', system-ui, sans-serif";
          ctx.fillText("attendance", cx, cy + 16);
          ctx.restore();
        }
      }]
    });
  }

  /* ---- 2. marks by subject (bars), with an exam selector ---- */
  if (barCanvas && data.bars) {
    var bars = data.bars;
    var examSelect = byId("exam-select"), barCaption = byId("marks-caption");
    var rowsFor = function (exam) { return bars.byExam[exam].rows; };
    var rows = rowsFor(bars.default);
    var barChart = new Chart(barCanvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: rows.map(function (r) { return r.subject; }),
        datasets: [
          { label: "Marks obtained", data: rows.map(function (r) { return r.obtained; }), rows: rows,
            backgroundColor: C.violet, hoverBackgroundColor: C.teal, borderRadius: 6, maxBarThickness: 38 },
          { label: "Maximum marks", data: rows.map(function (r) { return r.maximum; }), rows: rows,
            backgroundColor: "rgba(23, 37, 84, 0.14)", hoverBackgroundColor: "rgba(23, 37, 84, 0.28)",
            borderColor: C.navy, borderWidth: 1, borderRadius: 6, maxBarThickness: 38 }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: animation,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { beginAtZero: true, max: bars.byExam[bars.default].axisMax, title: axisTitle("Marks"), grid: { color: C.grid } },
          x: { grid: { display: false }, ticks: { color: C.text } }
        },
        plugins: {
          legend: legend,
          tooltip: {
            callbacks: {
              title: function (items) { return items[0].label; },
              label: function (ctx) {
                var r = ctx.dataset.rows[ctx.dataIndex];
                return ctx.datasetIndex === 0 ? "Marks obtained: " + r.obtained + " / " + r.maximum + " (" + r.percentage + "%)"
                                              : "Maximum marks: " + r.maximum;
              }
            }
          }
        }
      }
    });
    if (examSelect) {
      examSelect.addEventListener("change", function () {
        var exam = examSelect.value;
        if (!bars.byExam[exam]) { return; }
        var next = rowsFor(exam);
        barChart.data.labels = next.map(function (r) { return r.subject; });
        barChart.data.datasets[0].data = next.map(function (r) { return r.obtained; });
        barChart.data.datasets[0].rows = next;
        barChart.data.datasets[1].data = next.map(function (r) { return r.maximum; });
        barChart.data.datasets[1].rows = next;
        barChart.options.scales.y.max = bars.byExam[exam].axisMax;
        barChart.update();
        if (barCaption) { barCaption.textContent = bars.byExam[exam].caption; }
      });
    }
  }

  /* ---- 3. monthly attendance trend (only sent when there are at least two months of records) ---- */
  if (lineCanvas && data.monthly) {
    var mo = data.monthly;
    var pointColours = mo.labels.map(function (label) {
      return mo.highest.indexOf(label) >= 0 ? C.violet : (mo.lowest.indexOf(label) >= 0 ? C.coral : C.teal);
    });
    new Chart(lineCanvas.getContext("2d"), {
      type: "line",
      data: {
        labels: mo.labels,
        datasets: [{ label: "Attendance %", data: mo.percentages, borderColor: C.teal, backgroundColor: "rgba(13, 148, 136, 0.12)",
                     fill: true, tension: 0.25, borderWidth: 3, pointRadius: 6, pointHoverRadius: 9,
                     pointBackgroundColor: pointColours, pointBorderColor: "#ffffff", pointBorderWidth: 2 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: animation,
        scales: {
          y: { min: 0, max: 100, ticks: { stepSize: 20, callback: function (v) { return v + "%"; } },
               title: axisTitle("Attendance"), grid: { color: C.grid } },
          x: { title: axisTitle("Month (only months with attendance records)"), grid: { display: false } }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: function (items) { return items[0].label; },
              label: function (ctx) { return "Attendance: " + ctx.parsed.y + "%"; },
              afterLabel: function (ctx) { return mo.present[ctx.dataIndex] + " present out of " + mo.totals[ctx.dataIndex] + " valid records"; }
            }
          }
        }
      }
    });
  }
}());
