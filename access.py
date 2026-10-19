"""Data helpers and the rules about WHO may see or change WHAT.
Every route in the app goes through these functions."""
import datetime
import math
from collections import Counter, defaultdict

from flask import abort

import config
import grading
from db import col

NO_PASSWORD = {"password": 0}   # projection: never load the stored password field


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def parse_date(text):
    try:
        return datetime.datetime.strptime(text or "", "%Y-%m-%d")
    except ValueError:
        return None


def percent(part, total):
    return round(part * 100 / total, 1) if total else None


def paginate(collection, flt, sort, page, per_page=None, projection=None):
    per_page = per_page or config.PAGE_SIZE
    total = col(collection).count_documents(flt)
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    items = list(col(collection).find(flt, projection).sort(sort).skip((page - 1) * per_page).limit(per_page))
    return items, page, pages, total


def next_number(collection, field, prefix):
    """Highest number used so far in ids like MSG0600 (-> 600)."""
    highest = 0
    for d in col(collection).find({}, {field: 1}):
        value = str(d.get(field, ""))
        if value.startswith(prefix) and value[len(prefix):].isdigit():
            highest = max(highest, int(value[len(prefix):]))
    return highest


def audit(action, user, details):
    """Every change made through this app is recorded in the NEW collection audit_log."""
    col("audit_log").insert_one({"action": action, "userId": user["userId"], "role": user["role"],
                                 "details": details, "at": utc_now()})


# ---------------------------------------------------------------- parents
def parent_and_child(user):
    """The logged-in parent's record and THEIR OWN child. Aborts if the link is broken."""
    parent = col("parents").find_one({"parentId": user.get("referenceId")})
    if not parent or parent.get("status") != "Active":
        abort(403, "Your login is not linked to an active parent record. Please contact the school office.")
    student = col("students").find_one({"studentId": parent.get("studentId")})
    if not student or student.get("parentId") != parent["parentId"]:
        abort(403, "Your parent record is not linked to a student record. Please contact the school office.")
    return parent, student


def parent_for_student(student):
    """(parent record, parent login user) for a student, or (None, None) if either is missing."""
    parent = col("parents").find_one({"parentId": student.get("parentId"), "studentId": student["studentId"]})
    if not parent or parent.get("status") != "Active":
        return None, None
    user = col("users").find_one({"role": "Parent", "referenceId": parent["parentId"], "status": "Active"}, NO_PASSWORD)
    return (parent, user) if user else (None, None)


def notices_for_parent(student, page=1, per_page=10):
    """Notifications a parent may see: for everyone, for parents, or for their child's class."""
    flt = {"$or": [{"targetAudience": {"$in": ["All", "Parents"]}},
                   {"targetAudience": "Class", "classId": student["classId"]}]}
    return paginate("notifications", flt, [("createdDate", -1), ("notificationId", -1)], page, per_page)


# ---------------------------------------------------------------- teachers
def teacher_scope(user):
    teacher = col("teachers").find_one({"teacherId": user.get("referenceId")})
    if not teacher or teacher.get("status") != "Active":
        abort(403, "Your login is not linked to an active teacher record. Please contact the school office.")
    return TeacherScope(teacher)


class TeacherScope:
    """What one teacher is allowed to do.
    - A CLASS TEACHER (classes.classteacherid) may view all subjects of the students in that class.
    - A SUBJECT TEACHER (subjects.teacherId) may view and edit marks/attendance for that subject.
      Subjects are stored per grade (className), so it covers every section of that grade."""

    def __init__(self, teacher):
        self.teacher = teacher
        tid = teacher["teacherId"]
        self.led_classes = list(col("classes").find({"classteacherid": tid}))
        self.led_ids = {c["classid"] for c in self.led_classes}
        self.subjects = list(col("subjects").find({"teacherId": tid}))
        self.grades = {s["className"] for s in self.subjects}
        self._grade_subjects = {}

    def can_view_student(self, student):
        return student["classId"] in self.led_ids or student["className"] in self.grades

    def subject_ids_for(self, student):
        """Subjects of this student that the teacher may SEE."""
        if student["classId"] in self.led_ids:
            grade = student["className"]
            if grade not in self._grade_subjects:
                self._grade_subjects[grade] = [s["subjectId"] for s in col("subjects").find({"className": grade})]
            return self._grade_subjects[grade]
        return [s["subjectId"] for s in self.subjects if s["className"] == student["className"]]

    def editable_subject_ids(self, student):
        return [s["subjectId"] for s in self.subjects if s["className"] == student["className"]]

    def visible_classes(self):
        classes = col("classes").find({"$or": [{"classid": {"$in": list(self.led_ids)}},
                                               {"classname": {"$in": list(self.grades)}}]})
        return sorted(classes, key=lambda c: c["classid"])

    def students(self, class_id=None, query=""):
        flt = {"$or": [{"classId": {"$in": list(self.led_ids)}}, {"className": {"$in": list(self.grades)}}]}
        if class_id:
            flt = {"$and": [flt, {"classId": class_id}]}
        found = list(col("students").find(flt).sort([("classId", 1), ("rollNumber", 1)]))
        query = query.strip().lower()
        if query:
            found = [s for s in found if query in s["name"].lower() or query in s["studentId"].lower()]
        return found

    def edit_targets(self):
        """(class, subject) pairs this teacher may edit, e.g. English in 6A, 6B and 6C."""
        out = []
        for subject in self.subjects:
            for c in sorted(col("classes").find({"classname": subject["className"]}), key=lambda c: c["classid"]):
                out.append({"value": c["classid"] + ":" + subject["subjectId"], "class": c, "subject": subject,
                            "label": "%s - Class %s%s" % (subject["subjectName"], c["classname"], c["section"])})
        return out


def teachers_for_student(student):
    """Teachers a parent may message about this child: the class teacher and the
    teachers of the subjects taught to the child's grade (same rule TeacherScope uses)."""
    labels = defaultdict(list)
    cls = col("classes").find_one({"classid": student["classId"]})
    if cls:
        labels[cls["classteacherid"]].append("Class teacher")
    for s in col("subjects").find({"className": student["className"]}):
        labels[s["teacherId"]].append(s["subjectName"])
    ids = list(labels)
    teachers = {t["teacherId"]: t for t in col("teachers").find({"teacherId": {"$in": ids}})}
    users = {u["referenceId"]: u for u in col("users").find(
        {"role": "Teacher", "referenceId": {"$in": ids}, "status": "Active"}, NO_PASSWORD)}
    out = []
    for tid, label in labels.items():
        if tid in teachers and tid in users and teachers[tid].get("status") == "Active":
            out.append({"teacher": teachers[tid], "user": users[tid], "label": ", ".join(label)})
    return sorted(out, key=lambda o: o["teacher"]["name"])


# ---------------------------------------------------------------- marks and attendance summaries
def subjects_for_grade(grade):
    return {s["subjectId"]: s for s in col("subjects").find({"className": grade})}


def marks_by_exam(marks, subjects):
    """{exam: {"rows": [...], "average": x}} in a friendly exam order."""
    grouped = defaultdict(list)
    for m in marks:
        grouped[m["examName"]].append(m)
    result = {}
    for exam in sorted(grouped, key=grading.exam_sort_key):
        rows = []
        for m in sorted(grouped[exam], key=lambda m: m["subjectId"]):
            rows.append({"subject": subjects.get(m["subjectId"], {}).get("subjectName", m["subjectId"]),
                         "subjectId": m["subjectId"], "obtained": m["marksObtained"], "maximum": m["maximumMarks"],
                         "percentage": m["percentage"], "grade": m["grade"]})
        values = [r["percentage"] for r in rows]
        result[exam] = {"rows": rows, "average": round(sum(values) / len(values), 1) if values else None}
    return result


def attendance_by_subject(records, subjects):
    counts = defaultdict(Counter)
    for a in records:
        counts[a["subjectId"]][a["status"]] += 1
    rows = []
    for sid in sorted(counts):
        present, absent = counts[sid]["Present"], counts[sid]["Absent"]
        rows.append({"subject": subjects.get(sid, {}).get("subjectName", sid), "present": present,
                     "absent": absent, "total": present + absent, "percentage": percent(present, present + absent)})
    return rows


def attendance_totals(student_ids, visible=None):
    """{studentId: (present, total)}; `visible` optionally limits the subjects per student."""
    totals = defaultdict(lambda: [0, 0])
    for a in col("attendance").find({"studentId": {"$in": student_ids}}):
        if visible is not None and a["subjectId"] not in visible.get(a["studentId"], []):
            continue
        totals[a["studentId"]][1] += 1
        if a["status"] == "Present":
            totals[a["studentId"]][0] += 1
    return totals


def marks_average(student_ids, visible=None):
    values = defaultdict(list)
    for m in col("marks").find({"studentId": {"$in": student_ids}}):
        if visible is not None and m["subjectId"] not in visible.get(m["studentId"], []):
            continue
        values[m["studentId"]].append(m["percentage"])
    return {sid: round(sum(v) / len(v), 1) for sid, v in values.items()}


def message_lookup(messages):
    """Names for the user ids and student ids used in a list of messages."""
    user_ids = {m["senderId"] for m in messages} | {m["receiverId"] for m in messages}
    student_ids = {m["studentId"] for m in messages}
    users = {u["userId"]: u for u in col("users").find({"userId": {"$in": list(user_ids)}}, NO_PASSWORD)}
    students = {s["studentId"]: s for s in col("students").find({"studentId": {"$in": list(student_ids)}})}
    return users, students
