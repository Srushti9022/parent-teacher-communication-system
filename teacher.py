"""Teacher pages: students, marks entry, attendance entry."""
import datetime
import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

import access
import grading
from auth import role_required
from db import col

bp = Blueprint("teacher", __name__, url_prefix="/teacher")


@bp.route("/")
@role_required("Teacher")
def dashboard():
    sc = access.teacher_scope(g.user)
    uid = g.user["userId"]
    recent = list(col("messages").find({"receiverId": uid}).sort([("sentDate", -1), ("messageId", -1)]).limit(5))
    users, students = access.message_lookup(recent)
    return render_template("teacher/dashboard.html", sc=sc, student_count=len(sc.students()), recent=recent,
                           users=users, students=students,
                           unread=col("messages").count_documents({"receiverId": uid, "status": "Sent"}))


@bp.route("/students")
@role_required("Teacher")
def students():
    sc = access.teacher_scope(g.user)
    classes = sc.visible_classes()
    class_id = request.args.get("class_id", "")
    if class_id and class_id not in {c["classid"] for c in classes}:
        abort(403, "You do not have access to that class.")
    found = sc.students(class_id or None, request.args.get("q", ""))
    ids = [s["studentId"] for s in found]
    visible = {s["studentId"]: sc.subject_ids_for(s) for s in found}
    attendance = access.attendance_totals(ids, visible)
    averages = access.marks_average(ids, visible)
    rows = []
    for s in found:
        present, total = attendance.get(s["studentId"], (0, 0))
        rows.append({"s": s, "attendance": access.percent(present, total), "average": averages.get(s["studentId"])})
    return render_template("teacher/students.html", rows=rows, classes=classes, class_id=class_id,
                           q=request.args.get("q", ""), led_ids=sc.led_ids)


@bp.route("/students/<student_id>")
@role_required("Teacher")
def student_detail(student_id):
    sc = access.teacher_scope(g.user)
    student = col("students").find_one({"studentId": student_id})
    if not student:
        abort(404, "Student not found.")
    if not sc.can_view_student(student):
        abort(403, "You are not authorised to view this student.")
    visible = sc.subject_ids_for(student)
    subjects = access.subjects_for_grade(student["className"])
    marks = list(col("marks").find({"studentId": student_id, "subjectId": {"$in": visible}}))
    records = list(col("attendance").find({"studentId": student_id, "subjectId": {"$in": visible}}))
    parent, _ = access.parent_for_student(student)
    return render_template("teacher/student_detail.html", s=student, parent=parent,
                           exams=access.marks_by_exam(marks, subjects), subjects=subjects,
                           attendance=access.attendance_by_subject(records, subjects),
                           absences=sorted([a for a in records if a["status"] == "Absent"], key=lambda a: a["date"], reverse=True)[:10],
                           editable={s["subjectId"]: s["subjectName"] for s in sc.subjects if s["className"] == student["className"]}, class_id=student["classId"],
                           is_class_teacher=student["classId"] in sc.led_ids)


def _pick_target(sc, raw):
    for t in sc.edit_targets():
        if t["value"] == raw:
            return t
    return None


@bp.route("/marks", methods=["GET", "POST"])
@role_required("Teacher")
def marks():
    sc = access.teacher_scope(g.user)
    src = request.form if request.method == "POST" else request.args
    exams = sorted(col("marks").distinct("examName"), key=grading.exam_sort_key)
    target = _pick_target(sc, src.get("target", ""))
    exam = src.get("exam") if src.get("exam") in exams else None
    if src.get("target") and not target:
        abort(403, "You can only edit marks for the subjects you teach.")
    rows, errors = [], {}
    if target and exam:
        cls, subject = target["class"], target["subject"]
        studs = list(col("students").find({"classId": cls["classid"]}).sort("rollNumber", 1))
        found = {m["studentId"]: m for m in col("marks").find(
            {"studentId": {"$in": [s["studentId"] for s in studs]}, "subjectId": subject["subjectId"], "examName": exam})}
        rows = [{"s": s, "m": found.get(s["studentId"])} for s in studs]
        changes = []
        for r in rows:
            m = r["m"]
            if not m:
                continue
            r["entered"] = (request.form.get("mark_" + m["markId"], "") if request.method == "POST" else str(m["marksObtained"])).strip()
            if request.method == "POST":
                if not re.fullmatch(r"\d{1,3}", r["entered"]):
                    errors[m["markId"]] = "Enter a whole number."
                elif int(r["entered"]) > m["maximumMarks"]:
                    errors[m["markId"]] = "Maximum is %s." % m["maximumMarks"]
                elif int(r["entered"]) != m["marksObtained"]:
                    changes.append((m, int(r["entered"])))
        if request.method == "POST":
            if errors:
                flash("Nothing was saved. Fix the marked marks and save again.", "danger")
            elif not changes:
                flash("No changes to save.", "info")
            else:
                for m, value in changes:
                    pct = grading.percentage_for(value, m["maximumMarks"])
                    col("marks").update_one(
                        {"markId": m["markId"], "studentId": m["studentId"], "subjectId": subject["subjectId"], "examName": exam},
                        {"$set": {"marksObtained": value, "percentage": pct, "grade": grading.grade_for(pct)}})
                    access.audit("update_mark", g.user, {"markId": m["markId"], "studentId": m["studentId"],
                                                          "old": m["marksObtained"], "new": value})
                flash("Saved %d changed mark(s)." % len(changes), "success")
                return redirect(url_for("teacher.marks", target=target["value"], exam=exam))
    return render_template("teacher/marks.html", targets=sc.edit_targets(), exams=exams, target=target, exam=exam,
                           rows=rows, errors=errors)


@bp.route("/attendance", methods=["GET", "POST"])
@role_required("Teacher")
def attendance():
    sc = access.teacher_scope(g.user)
    src = request.form if request.method == "POST" else request.args
    target = _pick_target(sc, src.get("target", ""))
    if src.get("target") and not target:
        abort(403, "You can only edit attendance for the subjects you teach.")
    date_text = src.get("date") or datetime.date.today().isoformat()
    date = access.parse_date(date_text)
    rows, entered = [], {}
    if target:
        if not date:
            flash("Enter a valid date.", "danger")
        elif date.date() > datetime.date.today():
            flash("Attendance cannot be saved for a future date.", "danger")
            date = None
    if target and date:
        cls, subject = target["class"], target["subject"]
        studs = list(col("students").find({"classId": cls["classid"]}).sort("rollNumber", 1))
        existing = {a["studentId"]: a for a in col("attendance").find(
            {"studentId": {"$in": [s["studentId"] for s in studs]}, "subjectId": subject["subjectId"], "date": date})}
        for s in studs:
            if request.method == "POST":
                entered[s["studentId"]] = request.form.get("status_" + s["studentId"], "")
            elif s["studentId"] in existing:
                entered[s["studentId"]] = existing[s["studentId"]]["status"]
        rows = [{"s": s, "a": existing.get(s["studentId"])} for s in studs]
        if request.method == "POST":
            missing = [s["name"] for s in studs if entered[s["studentId"]] not in ("Present", "Absent")]
            if missing:
                flash("Choose Present or Absent for every student. Still missing: %s%s." %
                      (", ".join(missing[:5]), " and %d more" % (len(missing) - 5) if len(missing) > 5 else ""), "danger")
            else:
                updated, new_docs = 0, []
                number = access.next_number("attendance", "attendanceId", "AT")
                for s in studs:
                    status, old = entered[s["studentId"]], existing.get(s["studentId"])
                    if old and old["status"] != status:
                        col("attendance").update_one({"attendanceId": old["attendanceId"], "studentId": s["studentId"]},
                                                     {"$set": {"status": status}})
                        access.audit("update_attendance", g.user, {"attendanceId": old["attendanceId"], "old": old["status"], "new": status})
                        updated += 1
                    elif not old:
                        number += 1
                        new_docs.append({"attendanceId": "AT%06d" % number, "studentId": s["studentId"], "classId": cls["classid"],
                                         "subjectId": subject["subjectId"], "date": date, "status": status,
                                         "academicYear": s.get("academicYear", cls.get("academicyear"))})
                if new_docs:
                    col("attendance").insert_many(new_docs)
                    access.audit("create_attendance", g.user, {"count": len(new_docs), "classId": cls["classid"],
                                                                "subjectId": subject["subjectId"], "date": date_text})
                if updated or new_docs:
                    flash("Attendance saved: %d new record(s), %d changed." % (len(new_docs), updated), "success")
                else:
                    flash("No changes to save.", "info")
                return redirect(url_for("teacher.attendance", target=target["value"], date=date_text))
    return render_template("teacher/attendance.html", targets=sc.edit_targets(), target=target, date_text=date_text,
                           rows=rows, entered=entered, today=datetime.date.today().isoformat())
