"""Tests for the three dashboard charts (attendance doughnut, marks bars, monthly attendance line)."""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import progress   # noqa: E402

try:
    from test_app import PortalTest, _RAW   # noqa: E402
except ImportError:
    from tests.test_app import PortalTest, _RAW   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBJECTS = {"S1": {"subjectName": "Maths"}, "S2": {"subjectName": "Science"}}


def att(status, year, month, day=1, subject="S1"):
    return {"status": status, "date": dt.datetime(year, month, day), "subjectId": subject}


class TestInsightCalculations(unittest.TestCase):
    def test_pie_counts_and_percentages(self):
        recs = [att("Present", 2026, 7)] * 3 + [att("Absent", 2026, 7)]
        pie = progress.attendance_pie(progress.analyse_attendance(recs))
        self.assertEqual(pie, {"present": 3, "absent": 1, "total": 4, "percentage": 75.0, "presentPct": 75.0, "absentPct": 25.0})
        self.assertIsNone(progress.attendance_pie(progress.analyse_attendance([])))

    def test_invalid_attendance_records_are_never_counted(self):
        recs = [att("Present", 2026, 7), att("Absent", 2026, 7), {"status": "Late", "date": dt.datetime(2026, 7, 1), "subjectId": "S1"},
                {"status": "Present", "date": "2026-07-02", "subjectId": "S1"}, {"status": "Present", "subjectId": "S1"}, {"status": None, "date": None}]
        pie = progress.attendance_pie(progress.analyse_attendance(recs))
        self.assertEqual((pie["present"], pie["absent"], pie["total"]), (1, 1, 2))
        self.assertEqual(progress.monthly_attendance(recs)["skipped"], 4)

    def test_bars_use_progress_rules_and_python_figures(self):
        marks = [{"markId": "M1", "subjectId": "S1", "examName": "Unit Test 1", "marksObtained": 40, "maximumMarks": 50},
                 {"markId": "M2", "subjectId": "S2", "examName": "Unit Test 1", "marksObtained": 30, "maximumMarks": 50},
                 {"markId": "M3", "subjectId": "S1", "examName": "Mid-Term", "marksObtained": 90, "maximumMarks": 100},
                 {"markId": "M4", "subjectId": "S2", "examName": "Mid-Term", "marksObtained": 20, "maximumMarks": 0}]   # invalid: skipped
        bars = progress.marks_bars(progress.build_progress(marks, SUBJECTS))
        self.assertEqual((bars["exams"], bars["default"]), (["Unit Test 1", "Mid-Term"], "Mid-Term"))
        ut = bars["byExam"]["Unit Test 1"]
        self.assertEqual(ut["rows"], [{"subject": "Maths", "obtained": 40, "maximum": 50, "percentage": 80.0},
                                      {"subject": "Science", "obtained": 30, "maximum": 50, "percentage": 60.0}])
        self.assertEqual(ut["axisMax"], 50)
        self.assertEqual(ut["caption"], "Unit Test 1: 70 of 100 marks overall (70.0%) across 2 subjects.")
        self.assertEqual([r["subject"] for r in bars["byExam"]["Mid-Term"]["rows"]], ["Maths"], "invalid Science record left out")
        self.assertIsNone(progress.marks_bars(progress.build_progress([], SUBJECTS)))

    def test_monthly_needs_two_months_and_never_invents_months(self):
        self.assertIn("no valid attendance records", progress.monthly_attendance([])["message"])
        one = progress.monthly_attendance([att("Present", 2026, 7), att("Absent", 2026, 7, 8)])
        self.assertIsNone(one["chart"])
        self.assertIn("at least 2 different months", one["message"])
        self.assertIn("Jul 2026 (1 present, 50.0%)", one["message"])
        gap = progress.monthly_attendance([att("Present", 2026, 7), att("Absent", 2026, 9)])
        self.assertEqual(gap["chart"]["labels"], ["Jul 2026", "Sep 2026"], "August has no records, so it does not appear")

    def test_monthly_percentages_highest_lowest_and_order(self):
        recs = ([att("Present", 2026, 1)] * 3 + [att("Absent", 2026, 1)]            # Jan 2026: 3/4
                + [att("Present", 2025, 12)] * 2 + [att("Absent", 2025, 12)] * 2    # Dec 2025: 2/4
                + [att("Present", 2026, 2)] * 4)                                    # Feb 2026: 4/4
        m = progress.monthly_attendance(recs)
        self.assertEqual(m["chart"]["labels"], ["Dec 2025", "Jan 2026", "Feb 2026"], "chronological across years")
        self.assertEqual((m["chart"]["percentages"], m["chart"]["present"], m["chart"]["totals"]), ([50.0, 75.0, 100.0], [2, 3, 4], [4, 4, 4]))
        self.assertEqual((m["highest"], m["lowest"]), ({"labels": ["Feb 2026"], "percentage": 100.0}, {"labels": ["Dec 2025"], "percentage": 50.0}))

    def test_ties_and_equal_months(self):
        tied = progress.monthly_attendance([att("Present", 2026, 1), att("Present", 2026, 2), att("Absent", 2026, 3)])
        self.assertEqual(tied["highest"]["labels"], ["Jan 2026", "Feb 2026"])
        same = progress.monthly_attendance([att("Present", 2026, 1), att("Present", 2026, 2)])
        self.assertIsNone(same["highest"])
        self.assertIn("every month", same["note"])
        self.assertEqual(same["chart"]["highest"], [])


class TestInsightsOnDashboard(PortalTest):
    def data(self, client):
        html = client.get("/parent/").data.decode()
        return html, json.loads(re.search(r'id="insights-data">(.*?)</script>', html, re.S).group(1))

    def raw_attendance(self, sid):
        return [a for a in _RAW["attendance"] if a["studentId"] == sid]

    def add_months(self, sid="ST0001"):
        """TEST-ONLY: extra attendance in the in-memory copy so the line chart can be exercised."""
        extra = [{"attendanceId": "AT9%05d" % i, "studentId": sid, "classId": "C06A", "subjectId": "SUB0601", "academicYear": "2026-27",
                  "date": dt.datetime(2026, month, day), "status": status}
                 for i, (month, day, status) in enumerate([(8, 3, "Present"), (8, 4, "Present"), (8, 5, "Absent"), (8, 6, "Present"),
                                                           (9, 1, "Absent"), (9, 2, "Absent"), (9, 3, "Present"), (9, 4, "Absent")])]
        self.fake["attendance"].docs.extend(extra)
        return extra

    def test_pie_matches_the_real_records(self):
        html, data = self.data(self.parent("P0001"))
        recs = self.raw_attendance("ST0001")
        present = sum(1 for a in recs if a["status"] == "Present")
        self.assertEqual(data["pie"], {"present": present, "absent": len(recs) - present, "total": len(recs),
                                       "percentage": round(present * 100 / len(recs), 1), "presentPct": round(present * 100 / len(recs), 1),
                                       "absentPct": round((len(recs) - present) * 100 / len(recs), 1)})
        self.assertIn("Present: <strong>%d</strong> of %d" % (present, len(recs)), html)
        self.assertIn('id="attendance-pie"', html)

    def test_attendance_change_by_teacher_reaches_the_pie(self):
        teacher, parent = self.teacher("T001"), self.parent("P0001")
        before = self.data(parent)[1]["pie"]
        studs = [s["studentId"] for s in _RAW["students"] if s["classId"] == "C06A"]
        existing = {a["studentId"]: a["status"] for a in _RAW["attendance"] if a["subjectId"] == "SUB0601" and a["classId"] == "C06A"
                    and a["date"].isoformat().startswith("2026-07-06")}
        self.assertEqual(existing["ST0001"], "Absent")
        form = {"target": "C06A:SUB0601", "date": "2026-07-06"}
        form.update({"status_" + sid: existing[sid] for sid in studs}); form["status_ST0001"] = "Present"
        self.assertEqual(self.post(teacher, "/teacher/attendance", form).status_code, 302)
        after = self.data(parent)[1]["pie"]
        self.assertEqual((after["present"], after["absent"], after["total"]), (before["present"] + 1, before["absent"] - 1, before["total"]))
        self.assertEqual(len(self.fake["attendance"].docs), 14000)

    def test_bars_use_real_marks_and_subject_names(self):
        html, data = self.data(self.parent("P0001"))
        subjects = {s["subjectId"]: s["subjectName"] for s in _RAW["subjects"]}
        self.assertEqual(data["bars"]["exams"], ["Unit Test 1", "Mid-Term"])
        self.assertEqual(data["bars"]["default"], "Mid-Term")
        for exam in data["bars"]["exams"]:
            want = {subjects[m["subjectId"]]: (m["marksObtained"], m["maximumMarks"], m["percentage"])
                    for m in _RAW["marks"] if m["studentId"] == "ST0001" and m["examName"] == exam}
            got = {r["subject"]: (r["obtained"], r["maximum"], r["percentage"]) for r in data["bars"]["byExam"][exam]["rows"]}
            self.assertEqual(got, want, exam)
        self.assertIn('<option value="Unit Test 1" >Unit Test 1</option>', html.replace('<option value="Unit Test 1" >', '<option value="Unit Test 1" >'))
        self.assertIn('id="exam-select"', html)

    def test_marks_change_by_teacher_reaches_the_bars(self):
        teacher, parent = self.teacher("T001"), self.parent("P0001")
        ids = {s["studentId"] for s in _RAW["students"] if s["classId"] == "C06A"}
        form = {"target": "C06A:SUB0601", "exam": "Unit Test 1"}
        form.update({"mark_" + m["markId"]: str(30 if m["markId"] == "M000001" else m["marksObtained"]) for m in _RAW["marks"]
                     if m["studentId"] in ids and m["subjectId"] == "SUB0601" and m["examName"] == "Unit Test 1"})
        self.assertEqual(self.post(teacher, "/teacher/marks", form).status_code, 302)
        english = next(r for r in self.data(parent)[1]["bars"]["byExam"]["Unit Test 1"]["rows"] if r["subject"] == "English")
        self.assertEqual(english, {"subject": "English", "obtained": 30, "maximum": 50, "percentage": 60.0})
        self.assertEqual(len(self.fake["marks"].docs), 7000)

    def test_real_data_has_one_month_so_no_trend_chart_is_drawn(self):
        html, data = self.data(self.parent("P0001"))
        self.assertIsNone(data["monthly"])
        self.assertNotIn('id="monthly-trend"', html)
        self.assertIn("at least 2 different months", html)
        self.assertIn("All 28 valid records so far are in Jul 2026", html)
        self.assertRegex(html, r"<td>Jul 2026</td><td class=\"text-end\">\d+</td><td class=\"text-end\">28</td>")

    def test_monthly_chart_from_real_dates_and_statuses(self):
        extra = self.add_months()
        junk = [{"attendanceId": "AT99991", "studentId": "ST0001", "classId": "C06A", "subjectId": "SUB0601", "date": "2026-08-09", "status": "Present"},
                {"attendanceId": "AT99992", "studentId": "ST0001", "classId": "C06A", "subjectId": "SUB0601", "date": dt.datetime(2026, 8, 9), "status": "Late"}]
        self.fake["attendance"].docs.extend(junk)
        html, data = self.data(self.parent("P0001"))
        mo = data["monthly"]
        july = self.raw_attendance("ST0001")
        jp = sum(1 for a in july if a["status"] == "Present")
        self.assertEqual(mo["labels"], ["Jul 2026", "Aug 2026", "Sep 2026"])
        self.assertEqual(mo["present"], [jp, 3, 1])
        self.assertEqual(mo["totals"], [len(july), 4, 4])
        self.assertEqual(mo["percentages"], [round(jp * 100 / len(july), 1), 75.0, 25.0])
        self.assertEqual((mo["highest"], mo["lowest"]), (["Jul 2026"], ["Sep 2026"]))
        self.assertIn("Highest: Jul 2026 (%s%%)" % mo["percentages"][0], html)
        self.assertIn("Lowest: Sep 2026 (25.0%)", html)
        self.assertIn('id="monthly-trend"', html)
        self.assertIn("2 records without a valid date or status were left out.", html)
        self.assertEqual(data["pie"]["total"], len(july) + len(extra), "pie counts exactly the valid records")

    def test_empty_states_show_messages_not_charts(self):
        self.fake["attendance"].docs[:] = [a for a in self.fake["attendance"].docs if a["studentId"] != "ST0001"]
        self.fake["marks"].docs[:] = [m for m in self.fake["marks"].docs if m["studentId"] != "ST0001"]
        html, data = self.data(self.parent("P0001"))
        self.assertEqual(data, {"pie": None, "bars": None, "monthly": None})
        for canvas in ("attendance-pie", "marks-bar", "monthly-trend"):
            self.assertNotIn('id="%s"' % canvas, html)
        for text in ("No attendance has been recorded yet, so there is nothing to chart.",
                     "No exam results have been recorded yet, so there is nothing to chart.",
                     "no valid attendance records yet"):
            self.assertIn(text, html)

    def test_chart_data_belongs_to_the_logged_in_childs_records_only(self):
        c = self.parent("P0001")
        html = c.get("/parent/?student_id=ST0002&child=ST0002").data.decode()
        data = json.loads(re.search(r'id="insights-data">(.*?)</script>', html, re.S).group(1))
        mine = self.raw_attendance("ST0001")
        self.assertEqual(data["pie"]["total"], len(mine))
        self.assertEqual(data["pie"]["present"], sum(1 for a in mine if a["status"] == "Present"))
        block = re.search(r'id="insights-data">(.*?)</script>', html, re.S).group(1)
        for secret in ("ST0001", "ST0002", "P0001", "Aarav"):
            self.assertNotIn(secret, block)
        other = self.parent("P0002")
        child2 = next(s for s in _RAW["students"] if s["parentId"] == "P0002")["studentId"]
        self.assertEqual(self.data(other)[1]["pie"]["total"], len(self.raw_attendance(child2)))
        self.assertEqual(self.changed_collections(), set(), "viewing charts changes no data")

    def test_every_query_is_filtered_to_the_childs_id(self):
        from tests import fakedb
        calls = []
        original = fakedb.Collection.find
        names = {}
        def spy(self_, flt=None, projection=None):
            calls.append((names.get(id(self_)), flt)); return original(self_, flt, projection)
        parent = self.parent("P0001")
        names.update({id(col): n for n, col in self.fake.cols.items()})
        fakedb.Collection.find = spy
        try:
            parent.get("/parent/")
        finally:
            fakedb.Collection.find = original
        for name in ("marks", "attendance"):
            filters = [f for n, f in calls if n == name]
            self.assertEqual(filters, [{"studentId": "ST0001"}], name)

    def test_chart_scripts_run_with_real_dashboard_data(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        self.add_months()
        _, data = self.data(self.parent("P0001"))
        self.assertIsNotNone(data["monthly"])
        for payload, label in ((data, "with monthly"), ({**data, "monthly": None}, "without monthly")):
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(payload, f)
            result = subprocess.run(["node", os.path.join(ROOT, "tests", "insights_smoke.js"), f.name, os.path.join(ROOT, "static", "insights.js")],
                                    capture_output=True, text=True)
            os.unlink(f.name)
            self.assertEqual(result.returncode, 0, label + "\n" + result.stdout + result.stderr)
            self.assertIn("insights_smoke ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
