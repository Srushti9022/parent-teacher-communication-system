"""Run:  python -m unittest discover -s tests -v
The tests use your real database dump (read from DUMP_DIR, default ../database/smart_school)
loaded into memory. Your real MongoDB is never touched."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import access      # noqa: E402
import auth        # noqa: E402
import db          # noqa: E402
import grading     # noqa: E402
from app import create_app    # noqa: E402
from tests import fakedb      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DUMP = os.environ.get("DUMP_DIR") or os.path.join(HERE, "..", "..", "database", "smart_school")
NEW_PASSWORD = "Portal-Test-2026"
ORIGINAL = ["users", "students", "parents", "teachers", "classes", "subjects", "attendance", "marks", "messages", "notifications"]

_RAW = fakedb.load_dump(DUMP)


class PortalTest(unittest.TestCase):
    def setUp(self):
        self.fake = fakedb.FakeDB(_RAW)
        db.set_db(self.fake)
        auth._failures.clear()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.legacy = _RAW["users"][0]["password"]
        self.before = {name: copy.deepcopy(self.fake[name].docs) for name in ORIGINAL}

    # ---- helpers
    def email_of(self, role, ref):
        return next(u["email"] for u in _RAW["users"] if u["role"] == role and u["referenceId"] == ref)

    def post(self, client, url, data=None, follow=False):
        with client.session_transaction() as s:
            token = s.setdefault("_csrf", "test-token")
        return client.post(url, data=dict(data or {}, _csrf=token), follow_redirects=follow)

    def sign_in(self, email):
        """Sign in with the legacy password, then complete the forced password change."""
        client = self.app.test_client()
        r = self.post(client, "/login", {"email": email, "password": self.legacy})
        self.assertEqual(r.status_code, 302, "legacy sign-in should succeed")
        self.assertIn("/change-password", r.headers["Location"])
        r = self.post(client, "/change-password", {"current": self.legacy, "new": NEW_PASSWORD, "confirm": NEW_PASSWORD})
        self.assertEqual(r.status_code, 302)
        return client

    def parent(self, ref="P0001"):
        return self.sign_in(self.email_of("Parent", ref))

    def teacher(self, ref="T001"):
        return self.sign_in(self.email_of("Teacher", ref))

    def admin(self):
        return self.sign_in("admin@smartschool.edu")

    def changed_collections(self):
        return {n for n in ORIGINAL if self.fake[n].docs != self.before[n]}


class TestLoginAndPasswords(PortalTest):
    def test_legacy_password_is_never_changed_and_a_hash_is_stored_separately(self):
        client = self.app.test_client()
        r = self.post(client, "/login", {"email": "admin@smartschool.edu", "password": self.legacy})
        self.assertIn("/change-password", r.headers["Location"])
        self.assertEqual(client.get("/admin/").status_code, 302, "must change password before using the portal")
        cred = self.fake["app_credentials"].docs[0]
        self.assertNotEqual(cred["passwordHash"], self.legacy)
        self.assertNotIn(self.legacy, cred["passwordHash"])
        self.assertEqual(self.changed_collections(), set(), "no existing collection may change at sign-in")

    def test_after_changing_password_only_the_new_one_works(self):
        self.admin()
        c = self.app.test_client()
        self.assertEqual(self.post(c, "/login", {"email": "admin@smartschool.edu", "password": self.legacy}).status_code, 200)
        r = self.post(c, "/login", {"email": "admin@smartschool.edu", "password": NEW_PASSWORD})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("change-password", r.headers["Location"])

    def test_wrong_password_unknown_user_and_student_account(self):
        c = self.app.test_client()
        self.assertIn(b"Incorrect email or password", self.post(c, "/login", {"email": "admin@smartschool.edu", "password": "nope"}, True).data)
        self.assertIn(b"Incorrect email or password", self.post(c, "/login", {"email": "ghost@x.edu", "password": "nope"}, True).data)
        student_email = next(u["email"] for u in _RAW["users"] if u["role"] == "Student")
        r = self.post(c, "/login", {"email": student_email, "password": self.legacy}, True)
        self.assertIn(b"Admin, Teacher and Parent accounts only", r.data)
        self.assertEqual(len(self.fake["app_credentials"].docs), 0)

    def test_too_many_failures_locks_login_temporarily(self):
        c = self.app.test_client()
        for _ in range(5):
            self.post(c, "/login", {"email": "admin@smartschool.edu", "password": "wrong"})
        r = self.post(c, "/login", {"email": "admin@smartschool.edu", "password": self.legacy}, True)
        self.assertIn(b"Too many wrong attempts", r.data)

    def test_post_without_csrf_token_is_rejected(self):
        c = self.app.test_client()
        self.assertEqual(c.post("/login", data={"email": "a@b.c", "password": "x"}).status_code, 400)

    def test_password_rules(self):
        c = self.parent()
        r = self.post(c, "/change-password", {"current": NEW_PASSWORD, "new": "short", "confirm": "short"}, True)
        self.assertIn(b"8 to 128 characters", r.data)
        r = self.post(c, "/change-password", {"current": "wrong-current", "new": "Another-Pass-1", "confirm": "Another-Pass-1"}, True)
        self.assertIn(b"current password is not correct", r.data)


class TestRoleAccess(PortalTest):
    def test_signed_out_users_are_sent_to_login(self):
        c = self.app.test_client()
        for url in ("/admin/", "/teacher/", "/parent/", "/messages/"):
            r = c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login", r.headers["Location"])

    def test_each_role_only_reaches_its_own_pages(self):
        parent, teacher, admin = self.parent(), self.teacher(), self.admin()
        for url in ("/admin/", "/admin/users", "/teacher/", "/teacher/marks", "/teacher/attendance"):
            self.assertEqual(parent.get(url).status_code, 403, "parent -> " + url)
        for url in ("/admin/", "/admin/students", "/parent/", "/parent/marks"):
            self.assertEqual(teacher.get(url).status_code, 403, "teacher -> " + url)
        for url in ("/teacher/", "/parent/", "/messages/", "/messages/compose"):
            self.assertEqual(admin.get(url).status_code, 403, "admin -> " + url)

    def test_every_page_renders_for_its_role(self):
        pages = {self.admin(): ["/admin/", "/admin/users", "/admin/users?role=Parent&q=rajesh", "/admin/students?class_id=C06A",
                                "/admin/teachers", "/admin/classes", "/admin/notifications?audience=Class&priority=High",
                                "/admin/users?page=3"],
                 self.teacher(): ["/teacher/", "/teacher/students", "/teacher/students?class_id=C06A&q=aarav", "/teacher/students/ST0001",
                                  "/teacher/marks", "/teacher/marks?target=C06A:SUB0601&exam=Mid-Term",
                                  "/teacher/attendance", "/teacher/attendance?target=C06A:SUB0601&date=2026-07-06", "/messages/", "/messages/?box=sent",
                                  "/messages/compose"],
                 self.parent(): ["/parent/", "/parent/marks", "/parent/attendance", "/parent/notifications", "/messages/", "/messages/?box=sent",
                                 "/messages/compose"]}
        for client, urls in pages.items():
            for url in urls:
                r = client.get(url)
                self.assertEqual(r.status_code, 200, url)
                self.assertNotIn(self.legacy.encode(), r.data, "password text leaked on " + url)

    def test_admin_lists_never_show_passwords_or_change_data(self):
        admin = self.admin()
        body = admin.get("/admin/users").data
        self.assertNotIn(self.legacy.encode(), body)
        self.assertIn(b"admin@smartschool.edu", body)
        self.assertEqual(self.changed_collections(), set())


class TestParentIsolation(PortalTest):
    def test_parent_sees_only_own_child(self):
        c = self.parent("P0001")
        child = next(s for s in _RAW["students"] if s["studentId"] == "ST0001")["name"]
        other = next(s for s in _RAW["students"] if s["studentId"] == "ST0002")["name"]
        for url in ("/parent/", "/parent/marks", "/parent/attendance"):
            body = c.get(url).data.decode()
            self.assertIn(child, body)
            self.assertNotIn(other, body)
        body = c.get("/parent/attendance").data.decode()
        records = sum(1 for a in _RAW["attendance"] if a["studentId"] == "ST0001")
        subjects = len({a["subjectId"] for a in _RAW["attendance"] if a["studentId"] == "ST0001"})
        self.assertEqual(body.count("<tr><td>"), records + subjects, "all of the child's records, and nobody else's")

    def test_parent_notifications_are_filtered(self):
        _, child = access.parent_and_child({"referenceId": "P0001"})
        items, _, _, total = access.notices_for_parent(child, 1, 1000)
        self.assertEqual(total, len(items))
        self.assertTrue(all(n["targetAudience"] in ("All", "Parents") or (n["targetAudience"] == "Class" and n["classId"] == "C06A") for n in items))
        self.assertFalse(any(n["targetAudience"] in ("Teachers", "Students") for n in items))

    def test_parent_cannot_open_someone_elses_message_or_teacher_pages(self):
        c = self.parent("P0001")
        foreign = next(m for m in _RAW["messages"] if "U0542" not in (m["senderId"], m["receiverId"]))
        self.assertEqual(c.get("/messages/" + foreign["messageId"]).status_code, 404)
        self.assertEqual(self.post(c, "/messages/%s/status" % foreign["messageId"], {"status": "Read"}).status_code, 404)
        self.assertEqual(c.get("/teacher/students/ST0001").status_code, 403)
        self.assertEqual(self.changed_collections(), set())

    def test_parent_can_only_message_their_childs_teachers(self):
        c = self.parent("P0001")
        r = self.post(c, "/messages/compose", {"to": "T020", "subject": "Hello", "message": "Hello teacher"}, True)
        self.assertIn(b"Choose one of your child", r.data)
        self.assertEqual(len(self.fake["messages"].docs), 600)


class TestTeacherChangesReachParents(PortalTest):
    def _mark_form(self, class_id, subject_id, exam, overrides):
        ids = {s["studentId"] for s in _RAW["students"] if s["classId"] == class_id}
        data = {"target": "%s:%s" % (class_id, subject_id), "exam": exam}
        for m in _RAW["marks"]:
            if m["studentId"] in ids and m["subjectId"] == subject_id and m["examName"] == exam:
                data["mark_" + m["markId"]] = str(overrides.get(m["markId"], m["marksObtained"]))
        return data

    def test_marks_update_is_saved_and_visible_to_parent(self):
        teacher, parent = self.teacher("T001"), self.parent("P0001")
        old = next(m for m in _RAW["marks"] if m["markId"] == "M000001")
        self.assertEqual((old["marksObtained"], old["maximumMarks"]), (48, 50))
        self.assertIn(b'<td>English</td><td class="text-end">48 / 50', parent.get("/parent/marks").data)
        r = self.post(teacher, "/teacher/marks", self._mark_form("C06A", "SUB0601", "Unit Test 1", {"M000001": 30}))
        self.assertEqual(r.status_code, 302)
        doc = self.fake["marks"].find_one({"markId": "M000001"})
        self.assertEqual((doc["marksObtained"], doc["percentage"], doc["grade"]), (30, 60.0, "B"))
        self.assertIn(b'<td>English</td><td class="text-end">30 / 50', parent.get("/parent/marks").data)
        self.assertNotIn(b'<td>English</td><td class="text-end">48 / 50', parent.get("/parent/marks").data)
        self.assertEqual(self.fake["audit_log"].docs[0]["details"]["old"], 48)
        changed = [m["markId"] for m in self.fake["marks"].docs if m not in self.before["marks"]]
        self.assertEqual(changed, ["M000001"], "only the one edited mark may change")
        self.assertEqual(self.changed_collections(), {"marks"})

    def test_invalid_marks_are_rejected_and_nothing_is_saved(self):
        teacher = self.teacher("T001")
        for bad in ("51", "-3", "abc", ""):
            r = self.post(teacher, "/teacher/marks", self._mark_form("C06A", "SUB0601", "Unit Test 1", {"M000001": bad}), True)
            self.assertIn(b"Nothing was saved", r.data, bad)
        self.assertEqual(self.changed_collections(), set())

    def test_teacher_cannot_edit_subjects_they_do_not_teach(self):
        for ref, target in (("T020", "C06A:SUB0601"), ("T004", "C07A:SUB0701"), ("T001", "C07A:SUB0701")):
            c = self.teacher(ref)
            data = self._mark_form("C06A", "SUB0601", "Unit Test 1", {"M000001": 1})
            data["target"] = target
            self.assertEqual(self.post(c, "/teacher/marks", data).status_code, 403, ref)
            self.assertEqual(self.post(c, "/teacher/attendance", {"target": target, "date": "2026-07-06"}).status_code, 403, ref)
        self.assertEqual(self.changed_collections(), set())

    def test_class_teacher_sees_all_subjects_but_subject_teacher_only_theirs(self):
        class_teacher = self.teacher("T001").get("/teacher/students/ST0001").data.decode()
        for subject in ("English", "Mathematics", "Science", "Hindi"):
            self.assertIn(subject, class_teacher)
        subject_teacher = self.teacher("T002").get("/teacher/students/ST0001").data.decode()
        self.assertIn("Mathematics", subject_teacher)
        self.assertNotIn("Hindi", subject_teacher)
        self.assertEqual(self.teacher("T020").get("/teacher/students/ST0001").status_code, 403)

    def test_attendance_update_and_new_day(self):
        teacher, parent = self.teacher("T001"), self.parent("P0001")
        studs = [s["studentId"] for s in _RAW["students"] if s["classId"] == "C06A"]
        existing = {a["studentId"]: a["status"] for a in _RAW["attendance"] if a["subjectId"] == "SUB0601" and a["classId"] == "C06A"
                    and a["date"].isoformat().startswith("2026-07-06")}
        self.assertEqual(existing["ST0001"], "Absent")
        form = {"target": "C06A:SUB0601", "date": "2026-07-06"}
        form.update({"status_" + sid: existing[sid] for sid in studs})
        form["status_ST0001"] = "Present"
        self.assertEqual(self.post(teacher, "/teacher/attendance", form).status_code, 302)
        self.assertEqual(self.fake["attendance"].find_one({"attendanceId": "AT000001"})["status"], "Present")
        self.assertEqual(len(self.fake["attendance"].docs), 14000)
        # new date -> new records with the existing field layout
        form = {"target": "C06A:SUB0601", "date": "2026-10-09"}
        form.update({"status_" + sid: "Present" for sid in studs})
        form["status_ST0002"] = "Absent"
        self.assertEqual(self.post(teacher, "/teacher/attendance", form).status_code, 302)
        self.assertEqual(len(self.fake["attendance"].docs), 14000 + len(studs))
        new = self.fake["attendance"].find_one({"studentId": "ST0001", "subjectId": "SUB0601", "date": access.parse_date("2026-10-09")})
        self.assertEqual(sorted(new), sorted(_RAW["attendance"][0]), "same fields as existing records")
        self.assertIn(b"09 Oct 2026", parent.get("/parent/attendance").data)
        self.assertEqual(len({a["attendanceId"] for a in self.fake["attendance"].docs}), len(self.fake["attendance"].docs), "ids stay unique")
        self.assertEqual(self.changed_collections(), {"attendance"})

    def test_attendance_validation(self):
        teacher = self.teacher("T001")
        studs = [s["studentId"] for s in _RAW["students"] if s["classId"] == "C06A"]
        form = {"target": "C06A:SUB0601", "date": "2026-10-09"}
        form.update({"status_" + sid: "Present" for sid in studs[1:]})   # one student left blank
        self.assertIn(b"Choose Present or Absent", self.post(teacher, "/teacher/attendance", form, True).data)
        form = {"target": "C06A:SUB0601", "date": "2099-01-01"}
        form.update({"status_" + sid: "Present" for sid in studs})
        self.assertIn(b"future date", self.post(teacher, "/teacher/attendance", form, True).data)
        self.assertEqual(self.changed_collections(), set())

    def test_grade_scale_matches_every_existing_mark(self):
        for m in _RAW["marks"]:
            self.assertEqual(grading.percentage_for(m["marksObtained"], m["maximumMarks"]), m["percentage"], m["markId"])
            self.assertEqual(grading.grade_for(m["percentage"]), m["grade"], m["markId"])


class TestMessaging(PortalTest):
    def test_full_conversation(self):
        parent, teacher = self.parent("P0001"), self.teacher("T001")
        r = self.post(parent, "/messages/compose", {"to": "T001", "subject": "Maths help", "message": "Please advise on homework."})
        self.assertEqual(r.status_code, 302)
        m = self.fake["messages"].find_one({"messageId": "MSG0601"})
        self.assertEqual({k: m[k] for k in ("senderId", "senderRole", "receiverId", "receiverRole", "studentId", "status")},
                         {"senderId": "U0542", "senderRole": "parent", "receiverId": "U002", "receiverRole": "teacher",
                          "studentId": "ST0001", "status": "Sent"})
        self.assertEqual(sorted(m), sorted(_RAW["messages"][0]), "same fields as existing messages")
        self.assertIn(b"Maths help", teacher.get("/messages/").data)
        self.assertEqual(self.post(teacher, "/messages/MSG0601/status", {"status": "Sent"}).status_code, 302)
        self.assertEqual(teacher.get("/messages/MSG0601").status_code, 200)
        self.assertEqual(self.fake["messages"].find_one({"messageId": "MSG0601"})["status"], "Read")
        # the sender cannot change the receiver's read status
        self.assertEqual(self.post(parent, "/messages/MSG0601/status", {"status": "Read"}).status_code, 404)
        # teacher replies about the same student
        self.assertEqual(self.post(teacher, "/messages/compose", {"to": "ST0001", "subject": "Re: Maths help", "message": "Sure, see me Friday."}).status_code, 302)
        reply = self.fake["messages"].find_one({"messageId": "MSG0602"})
        self.assertEqual((reply["senderId"], reply["receiverId"], reply["receiverRole"]), ("U002", "U0542", "parent"))
        self.assertIn(b"Sure, see me Friday.", parent.get("/messages/MSG0602").data)
        self.assertEqual(self.changed_collections(), {"messages"})
        untouched = [m for m in self.before["messages"] if m in self.fake["messages"].docs]
        self.assertEqual(len(untouched), 600, "existing messages unchanged")

    def test_teacher_cannot_message_about_students_outside_their_classes(self):
        c = self.teacher("T020")
        r = self.post(c, "/messages/compose", {"to": "ST0001", "subject": "Hello", "message": "Hello parent"}, True)
        self.assertIn(b"Choose a student from your own classes", r.data)
        self.assertEqual(len(self.fake["messages"].docs), 600)

    def test_message_validation(self):
        c = self.parent("P0001")
        self.assertIn(b"subject must be", self.post(c, "/messages/compose", {"to": "T001", "subject": "x", "message": "long enough"}, True).data)
        self.assertIn(b"message must be", self.post(c, "/messages/compose", {"to": "T001", "subject": "Fine", "message": "hi"}, True).data)
        self.assertEqual(len(self.fake["messages"].docs), 600)

    def test_existing_messages_follow_the_rules_the_app_enforces(self):
        users = {u["userId"]: u for u in _RAW["users"]}
        students = {s["studentId"]: s for s in _RAW["students"]}
        for m in _RAW["messages"]:
            teacher_user = users[m["senderId"] if m["senderRole"] == "teacher" else m["receiverId"]]
            parent_user = users[m["senderId"] if m["senderRole"] == "parent" else m["receiverId"]]
            self.assertEqual((teacher_user["role"], parent_user["role"]), ("Teacher", "Parent"))
            self.assertEqual(parent_user["referenceId"], students[m["studentId"]]["parentId"])


if __name__ == "__main__":
    unittest.main()
