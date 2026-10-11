"""Parent pages. Every query is filtered by the signed-in parent's OWN child."""
from flask import Blueprint, g, render_template, request

import access
from auth import role_required
from db import col

bp = Blueprint("parent", __name__, url_prefix="/parent")


@bp.route("/")
@role_required("Parent")
def dashboard():
    parent, child = access.parent_and_child(g.user)
    sid = child["studentId"]
    subjects = access.subjects_for_grade(child["className"])
    marks = list(col("marks").find({"studentId": sid}))
    records = list(col("attendance").find({"studentId": sid}))
    present = sum(1 for a in records if a["status"] == "Present")
    cls = col("classes").find_one({"classid": child["classId"]})
    class_teacher = col("teachers").find_one({"teacherId": cls["classteacherid"]}) if cls else None
    notices = access.notices_for_parent(child, 1, 4)[0]
    return render_template("parent/dashboard.html", child=child, parent=parent, class_teacher=class_teacher,
                           exams=access.marks_by_exam(marks, subjects), attendance_pct=access.percent(present, len(records)),
                           attendance_count=len(records), last_date=max((a["date"] for a in records), default=None),
                           notices=notices, unread=col("messages").count_documents(
                               {"receiverId": g.user["userId"], "studentId": sid, "status": "Sent"}))


@bp.route("/marks")
@role_required("Parent")
def marks():
    _, child = access.parent_and_child(g.user)
    subjects = access.subjects_for_grade(child["className"])
    marks = list(col("marks").find({"studentId": child["studentId"]}))
    return render_template("parent/marks.html", child=child, exams=access.marks_by_exam(marks, subjects))


@bp.route("/attendance")
@role_required("Parent")
def attendance():
    _, child = access.parent_and_child(g.user)
    subjects = access.subjects_for_grade(child["className"])
    records = list(col("attendance").find({"studentId": child["studentId"]}))
    present = sum(1 for a in records if a["status"] == "Present")
    records.sort(key=lambda a: (a["date"], a["subjectId"]), reverse=True)
    return render_template("parent/attendance.html", child=child, summary=access.attendance_by_subject(records, subjects),
                           records=records, subjects=subjects, overall=access.percent(present, len(records)))


@bp.route("/notifications")
@role_required("Parent")
def notifications():
    _, child = access.parent_and_child(g.user)
    items, page, pages, total = access.notices_for_parent(child, request.args.get("page", 1, type=int), 10)
    creators = {u["userId"]: u for u in col("users").find({"userId": {"$in": [n["createdBy"] for n in items]}}, access.NO_PASSWORD)}
    return render_template("parent/notifications.html", child=child, items=items, page=page, pages=pages, total=total, creators=creators)
