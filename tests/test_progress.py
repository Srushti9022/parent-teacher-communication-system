"""Tests for the parent progress dashboard. Run with the others:
   python -m unittest discover -s tests -v"""
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
    from test_app import PortalTest, _RAW, NEW_PASSWORD   # noqa: E402,F401
except ImportError:
    from tests.test_app import PortalTest, _RAW, NEW_PASSWORD   # noqa: E402,F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBJECTS = {"S1": {"subjectName": "Maths"}, "S2": {"subjectName": "Science"}, "S3": {"subjectName": "English"}}


def mark(mid, subject, exam, obtained, maximum):
    return {"markId": mid, "subjectId": subject, "examName": exam, "marksObtained": obtained, "maximumMarks": maximum}


def make(exam_scores, maximum=100):
    """{'Unit Test 1': [65, 65, 65], 'Mid-Term': [72, 72, 72]} -> marks documents"""
    out, n = [], 0
    for exam, scores in exam_scores.items():
        for i, s in enumerate(scores):
            n += 1
            out.append(mark("M%03d" % n, "S%d" % (i + 1), exam, s, maximum))
    return out


class TestCalculations(unittest.TestCase):
    def test_overall_is_total_over_total_not_average_of_percentages(self):
        marks = [mark("M1", "S1", "Unit Test 1", 40, 50), mark("M2", "S2", "Unit Test 1", 30, 100)]
        p = progress.build_progress(marks, SUBJECTS)
        self.assertEqual(p["overall"][0]["percentage"], 46.7)      # 70 / 150, NOT (80 + 30) / 2 = 55
        self.assertEqual((p["overall"][0]["obtained"], p["overall"][0]["maximum"]), (70, 150))

    def test_invalid_records_are_skipped_never_zero(self):
        bad = [mark("M1", "S1", "Unit Test 1", 10, 0), mark("M2", "S2", "Unit Test 1", 60, 50),
               mark("M3", "S3", "Unit Test 1", None, 50), mark("M4", "S1", "Mid-Term", "x", 50),
               mark("M5", "S2", "Mid-Term", -1, 50), mark("M6", "S3", "Mid-Term", True, 50)]
        p = progress.build_progress(bad, SUBJECTS)
        self.assertFalse(p["hasData"])
        self.assertEqual(p["exams"], [])
        self.assertEqual(p["quality"]["invalidSkipped"], 6)
        good = bad + [mark("M7", "S1", "Unit Test 1", 25, 50)]
        p = progress.build_progress(good, SUBJECTS)
        self.assertEqual((p["exams"], p["overall"][0]["percentage"], p["overall"][0]["subjects"]), (["Unit Test 1"], 50.0, 1))

    def test_duplicate_subject_exam_records_are_not_counted_twice(self):
        marks = [mark("M1", "S1", "Unit Test 1", 20, 50), mark("M2", "S1", "Unit Test 1", 45, 50)]
        p = progress.build_progress(marks, SUBJECTS)
        self.assertEqual(p["overall"][0]["maximum"], 50)
        self.assertEqual(p["overall"][0]["percentage"], 90.0, "the highest markId is the one used")
        self.assertEqual(p["quality"]["duplicatesIgnored"], 1)

    def test_exam_order_is_not_alphabetical(self):
        marks = [mark("M1", "S1", "Mid-Term", 50, 100), mark("M2", "S1", "Final", 60, 100), mark("M3", "S1", "Unit Test 1", 40, 100)]
        self.assertEqual(progress.build_progress(marks, SUBJECTS)["exams"], ["Unit Test 1", "Mid-Term", "Final"])
        self.assertIn("not date-verified", progress.build_progress(marks, SUBJECTS)["chronologyNote"])

    def test_change_is_percentage_points_and_status(self):
        s = progress.build_progress(make({"Unit Test 1": [65] * 3, "Mid-Term": [72] * 3}), SUBJECTS)["summary"]
        self.assertEqual((s["change"], s["changeText"], s["status"]), (7.0, "+7.0", "Improving"))
        self.assertEqual((s["latest"]["percentage"], s["previous"]["percentage"]), (72.0, 65.0))
        down = progress.build_progress(make({"Unit Test 1": [80] * 3, "Mid-Term": [60] * 3}), SUBJECTS)["summary"]
        self.assertEqual((down["change"], down["status"], down["changeText"]), (-20.0, "Declining", "\u221220.0"))
        for latest, status in ((66.9, "Stable"), (67.0, "Improving"), (63.0, "Declining"), (63.1, "Stable"), (65.0, "Stable")):
            marks = [mark("M1", "S1", "Unit Test 1", 65, 100), mark("M2", "S1", "Mid-Term", latest, 100)]
            self.assertEqual(progress.build_progress(marks, SUBJECTS)["summary"]["status"], status, latest)

    def test_one_exam_and_no_exams_never_invent_a_previous_result(self):
        one = progress.build_progress(make({"Unit Test 1": [70, 80, 90]}), SUBJECTS)
        self.assertEqual((one["summary"]["previous"], one["summary"]["change"], one["summary"]["status"]), (None, None, None))
        self.assertTrue(one["hasData"])
        none = progress.build_progress([], SUBJECTS)
        self.assertFalse(none["hasData"])
        self.assertIsNone(none["summary"]["latest"])
        self.assertEqual(none["overall"], [])
        report = progress.build_report(none, progress.analyse_attendance([]), {})
        self.assertIn("nothing to summarise", report["summary"])
        self.assertIn("No attendance", report["attendance_summary"])

    def test_strongest_and_weakest_need_three_subjects(self):
        two = progress.build_progress([mark("M1", "S1", "Unit Test 1", 90, 100), mark("M2", "S2", "Unit Test 1", 40, 100)], SUBJECTS)["summary"]
        self.assertIsNone(two["strongest"])
        three = progress.build_progress(make({"Unit Test 1": [90, 40, 70]}), SUBJECTS)["summary"]
        self.assertEqual((three["strongest"]["names"], three["attention"]["names"]), (["Maths"], ["Science"]))
        same = progress.build_progress(make({"Unit Test 1": [70, 70, 70]}), SUBJECTS)["summary"]
        self.assertIsNone(same["strongest"])

    def test_missing_subject_in_latest_exam_is_marked_not_recorded(self):
        marks = make({"Unit Test 1": [70, 80, 90]}) + [mark("M9", "S1", "Mid-Term", 60, 100), mark("M10", "S2", "Mid-Term", 70, 100)]
        p = progress.build_progress(marks, SUBJECTS)
        rows = {r["id"]: r for r in p["latest_rows"]}
        self.assertFalse(rows["S3"]["recorded"])
        self.assertIsNone(rows["S3"]["percentage"])
        self.assertEqual(p["overall"][1]["subjects"], 2)
        self.assertFalse(p["summary"]["likeForLike"])
        english = next(s for s in p["series"] if s["id"] == "S3")
        self.assertIsNone(english["points"][1], "missing result is None, not zero")

    def test_careers_need_consistent_strength_across_two_exams(self):
        strong = progress.build_progress(make({"Unit Test 1": [90, 50, 60], "Mid-Term": [85, 55, 40]}), SUBJECTS)
        r = progress.build_report(strong, progress.analyse_attendance([]), {})
        self.assertTrue(r["careers"]["enough"])
        self.assertEqual([i["subject"] for i in r["careers"]["entries"]], ["Maths"])
        once = progress.build_progress(make({"Unit Test 1": [90, 50, 60], "Mid-Term": [60, 55, 40]}), SUBJECTS)
        self.assertFalse(progress.build_report(once, progress.analyse_attendance([]), {})["careers"]["enough"])
        single = progress.build_progress(make({"Unit Test 1": [95, 95, 95]}), SUBJECTS)
        self.assertIn("Not enough evidence", progress.build_report(single, progress.analyse_attendance([]), {})["careers"]["message"])

    def test_attendance_analysis(self):
        import datetime as dt
        d1, d2 = dt.datetime(2026, 7, 6), dt.datetime(2026, 7, 8)
        recs = [{"date": d1, "status": "Present", "subjectId": "S1"}, {"date": d1, "status": "Absent", "subjectId": "S2"},
                {"date": d2, "status": "Absent", "subjectId": "S1"}, {"date": d2, "status": "Absent", "subjectId": "S2"},
                {"date": d2, "status": "Present", "subjectId": "S3"}, {"date": d2, "status": "weird", "subjectId": "S3"}]
        a = progress.analyse_attendance(recs)
        self.assertEqual((a["total"], a["present"], a["percentage"], a["days"]), (5, 2, 40.0, 2))
        self.assertEqual(a["worst_day"]["absent"], 2)

    def test_every_student_in_the_dump_computes_cleanly(self):
        by_student = {}
        for m in _RAW["marks"]:
            by_student.setdefault(m["studentId"], []).append(m)
        subjects = {}
        for s in _RAW["subjects"]:
            subjects.setdefault(s["className"], {})[s["subjectId"]] = s
        for st in _RAW["students"]:
            p = progress.build_progress(by_student[st["studentId"]], subjects[st["className"]])
            self.assertEqual(p["exams"], ["Unit Test 1", "Mid-Term"])
            self.assertTrue(all(0 <= o["percentage"] <= 100 for o in p["overall"]))
            self.assertEqual(p["quality"], {"duplicatesIgnored": 0, "invalidSkipped": 0})
            report = progress.build_report(p, progress.analyse_attendance([]), {})
            self.assertTrue(report["summary"])


class TestParentDashboard(PortalTest):
    def chart_json(self, html):
        return json.loads(re.search(r'<script type="application/json" id="progress-data">(.*?)</script>', html, re.S).group(1))

    def expected_overall(self, student_id, exam):
        items = [m for m in _RAW["marks"] if m["studentId"] == student_id and m["examName"] == exam]
        return round(sum(m["marksObtained"] for m in items) * 100 / sum(m["maximumMarks"] for m in items), 1)

    def dashboard(self, client):
        r = client.get("/parent/")
        self.assertEqual(r.status_code, 200)
        return r.data.decode()

    def mark_form(self, overrides):
        ids = {s["studentId"] for s in _RAW["students"] if s["classId"] == "C06A"}
        data = {"target": "C06A:SUB0601", "exam": "Unit Test 1"}
        for m in _RAW["marks"]:
            if m["studentId"] in ids and m["subjectId"] == "SUB0601" and m["examName"] == "Unit Test 1":
                data["mark_" + m["markId"]] = str(overrides.get(m["markId"], m["marksObtained"]))
        return data

    def test_dashboard_shows_real_data_for_the_linked_child(self):
        html = self.dashboard(self.parent("P0001"))
        self.assertIn("My Child's Academic Progress", html)
        data = self.chart_json(html)
        ut, mt = self.expected_overall("ST0001", "Unit Test 1"), self.expected_overall("ST0001", "Mid-Term")
        self.assertEqual(data["exams"], ["Unit Test 1", "Mid-Term"])
        self.assertEqual([p["percentage"] for p in data["overall"]], [ut, mt])
        self.assertEqual([s["name"] for s in data["subjects"]], ["English", "Mathematics", "Science", "Social Science", "Hindi", "Marathi", "Computer"])
        for name in ("English", "Mathematics", "Computer"):
            self.assertIn('>%s</option>' % name, html, "subject selector option")
        self.assertIn("Declining", html)
        self.assertIn("\u2212%.1f" % abs(round(mt - ut, 1)), html)
        self.assertIn("Rule-based report", html)
        self.assertIn("No AI service is used", html)
        self.assertIn("not date-verified", html)
        for link in ("/parent/marks", "/parent/attendance", "/messages/", "/parent/notifications", "/messages/compose"):
            self.assertIn('href="%s"' % link, html)

    def test_dashboard_links_still_work(self):
        c = self.parent("P0001")
        for link in re.findall(r'href="(/(?:parent|messages)[^"]*)"', self.dashboard(c)):
            self.assertEqual(c.get(link).status_code, 200, link)

    def test_teacher_update_reaches_parent_dashboard_without_duplicates(self):
        teacher, parent = self.teacher("T001"), self.parent("P0001")
        before = self.chart_json(self.dashboard(parent))
        self.assertEqual(before["subjects"][0]["points"][0]["percentage"], 96.0)
        self.assertEqual(self.post(teacher, "/teacher/marks", self.mark_form({"M000001": 30})).status_code, 302)
        self.assertEqual(len(self.fake["marks"].docs), 7000, "update, not insert")
        html = self.dashboard(parent)
        after = self.chart_json(html)
        self.assertEqual(after["subjects"][0]["points"][0], {"obtained": 30, "maximum": 50, "percentage": 60.0})
        self.assertEqual(after["overall"][0]["percentage"], round((before["overall"][0]["obtained"] - 18) * 100 / 350, 1))
        self.assertEqual(after["overall"][1], before["overall"][1], "other exam untouched")
        new_ut, mt = after["overall"][0]["percentage"], after["overall"][1]["percentage"]
        self.assertIn("overall %s%% to %s%%" % (new_ut, mt), html, "report rebuilt from the new marks")
        self.assertIn("%s%% in Unit Test 1" % new_ut, html)
        self.assertNotIn("82.0", html, "the old overall percentage must not appear anywhere")
        self.post(teacher, "/teacher/marks", self.mark_form({"M000001": 40}))     # a second update
        again = self.chart_json(self.dashboard(parent))
        self.assertEqual(again["subjects"][0]["points"][0]["percentage"], 80.0)
        self.assertEqual(len(self.fake["marks"].docs), 7000)
        self.assertEqual(len([m for m in self.fake["marks"].docs if m["studentId"] == "ST0001" and m["subjectId"] == "SUB0601"
                              and m["examName"] == "Unit Test 1"]), 1)

    def test_parent_cannot_reach_another_childs_data(self):
        c = self.parent("P0001")
        other_student = next(s for s in _RAW["students"] if s["studentId"] == "ST0002")
        html = c.get("/parent/?student_id=ST0002&studentId=ST0002&child=ST0002").data.decode()
        self.assertNotIn(other_student["name"], html)
        data = self.chart_json(html)
        self.assertEqual(data["overall"][0]["percentage"], self.expected_overall("ST0001", "Unit Test 1"))
        self.assertNotEqual(self.expected_overall("ST0001", "Unit Test 1"), self.expected_overall("ST0002", "Unit Test 1"))
        block = re.search(r'id="progress-data">(.*?)</script>', html, re.S).group(1)
        for secret in ("ST0001", "ST0002", other_student["name"], "Aarav", "P0001"):
            self.assertNotIn(secret, block, "chart data carries no names or ids")
        second = self.parent("P0002")
        child2 = next(s for s in _RAW["students"] if s["parentId"] == "P0002")["studentId"]
        self.assertEqual(self.chart_json(self.dashboard(second))["overall"][0]["percentage"], self.expected_overall(child2, "Unit Test 1"))
        self.assertEqual(c.get("/parent/progress").status_code, 404, "no separate data endpoint exists")
        self.assertEqual(self.teacher("T001").get("/parent/").status_code, 403)
        self.assertEqual(self.admin().get("/parent/").status_code, 403)

    def test_no_marks_shows_empty_state_not_zeros(self):
        self.fake["marks"].docs[:] = [m for m in self.fake["marks"].docs if m["studentId"] != "ST0001"]
        html = self.dashboard(self.parent("P0001"))
        self.assertIn("No exam results yet", html)
        self.assertIn("No results recorded yet", html)
        self.assertNotIn('id="progress-chart"', html)
        self.assertNotIn('id="progress-data"', html)
        self.assertIn("nothing to summarise", html)
        self.assertIn("Not enough evidence", html)

    def test_no_attendance_is_handled(self):
        self.fake["attendance"].docs[:] = [a for a in self.fake["attendance"].docs if a["studentId"] != "ST0001"]
        html = self.dashboard(self.parent("P0001"))
        self.assertIn("No attendance recorded yet", html)
        self.assertIn("No attendance has been recorded yet.", html)

    def test_one_exam_only(self):
        self.fake["marks"].docs[:] = [m for m in self.fake["marks"].docs if not (m["studentId"] == "ST0001" and m["examName"] == "Mid-Term")]
        html = self.dashboard(self.parent("P0001"))
        data = self.chart_json(html)
        self.assertEqual(data["exams"], ["Unit Test 1"])
        self.assertIn("Only one exam is recorded so far", html)
        self.assertIn("No earlier exam yet", html)
        self.assertIn("Needs a second exam to compare", html)
        self.assertNotIn("Improving", html)
        self.assertNotIn("Declining", html)

    def test_incomplete_latest_exam(self):
        self.fake["marks"].docs[:] = [m for m in self.fake["marks"].docs if not (
            m["studentId"] == "ST0001" and m["examName"] == "Mid-Term" and m["subjectId"] == "SUB0603")]
        html = self.dashboard(self.parent("P0001"))
        self.assertIn("Not recorded for Mid-Term yet", html)
        self.assertIn("not like for like", html)
        self.assertEqual(self.chart_json(html)["overall"][1]["subjects"], 6)

    def test_chart_script_runs_with_the_real_dashboard_data(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        data = self.chart_json(self.dashboard(self.parent("P0001")))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
        result = subprocess.run(["node", os.path.join(ROOT, "tests", "chart_smoke.js"), f.name, os.path.join(ROOT, "static", "progress.js")],
                                capture_output=True, text=True)
        os.unlink(f.name)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("chart_smoke ok", result.stdout)

    def test_existing_data_is_untouched_by_viewing_the_dashboard(self):
        self.dashboard(self.parent("P0001"))
        self.assertEqual(self.changed_collections(), set())


if __name__ == "__main__":
    unittest.main()
