"""Admin pages (read-only): dashboard, users, students, teachers, classes, notifications."""
import re
from collections import Counter

from flask import Blueprint, render_template, request

import access
from auth import role_required
from db import col

bp = Blueprint("admin", __name__, url_prefix="/admin")
BADGE = {"Active": "success", "Inactive": "secondary", "High": "danger", "Normal": "primary", "Low": "secondary",
         "Admin": "dark", "Teacher": "info", "Parent": "success", "Student": "secondary"}


def badge(text):
    return {"text": text, "badge": BADGE.get(text, "secondary")}


def regex(text):
    return {"$regex": re.escape(text), "$options": "i"}


@bp.route("/")
@role_required("Admin")
def dashboard():
    roles = {r: col("users").count_documents({"role": r}) for r in ("Admin", "Teacher", "Parent", "Student")}
    per_class = Counter(s["classId"] for s in col("students").find({}, {"classId": 1}))
    teachers = {t["teacherId"]: t["name"] for t in col("teachers").find()}
    classes = [{"c": c, "students": per_class.get(c["classid"], 0), "teacher": teachers.get(c["classteacherid"], "-")}
               for c in col("classes").find().sort("classid", 1)]
    stats = {"Students": col("students").count_documents({}), "Teachers": col("teachers").count_documents({}),
             "Parents": col("parents").count_documents({}), "Classes": col("classes").count_documents({}),
             "Messages": col("messages").count_documents({}),
             "Unread messages": col("messages").count_documents({"status": "Sent"})}
    recent = list(col("notifications").find().sort([("createdDate", -1), ("notificationId", -1)]).limit(5))
    return render_template("admin/dashboard.html", stats=stats, roles=roles, classes=classes, recent=recent)


@bp.route("/users")
@role_required("Admin")
def users():
    role, q = request.args.get("role", ""), request.args.get("q", "").strip()
    flt = {}
    if role in ("Admin", "Teacher", "Parent", "Student"):
        flt["role"] = role
    if q:
        flt["$or"] = [{"name": regex(q)}, {"email": regex(q)}, {"userId": regex(q)}]
    # NOTE: the stored password field is never loaded or shown (projection NO_PASSWORD).
    items, page, pages, total = access.paginate("users", flt, [("userId", 1)], request.args.get("page", 1, type=int),
                                                projection=access.NO_PASSWORD)
    rows = [[u["userId"], u["name"], u["email"], badge(u["role"]), u.get("referenceId", ""), badge(u["status"])] for u in items]
    return render_template("admin/list.html", title="Users", columns=["User ID", "Name", "Email", "Role", "Linked record", "Status"],
                           rows=rows, page=page, pages=pages, total=total,
                           filters=[{"name": "role", "label": "Role", "options": ["Admin", "Teacher", "Parent", "Student"]},
                                    {"name": "q", "label": "Search name, email or ID", "text": True}])


@bp.route("/students")
@role_required("Admin")
def students():
    class_id, q = request.args.get("class_id", ""), request.args.get("q", "").strip()
    flt = {}
    if class_id:
        flt["classId"] = class_id
    if q:
        flt["$or"] = [{"name": regex(q)}, {"studentId": regex(q)}]
    items, page, pages, total = access.paginate("students", flt, [("classId", 1), ("rollNumber", 1)],
                                                request.args.get("page", 1, type=int))
    parents = {p["parentId"]: p["name"] for p in col("parents").find({"parentId": {"$in": [s["parentId"] for s in items]}})}
    rows = [[s["studentId"], s["name"], "%s%s" % (s["className"], s["section"]), s["rollNumber"], s["gender"],
             parents.get(s["parentId"], "-"), badge(s["status"])] for s in items]
    class_ids = [c["classid"] for c in col("classes").find().sort("classid", 1)]
    return render_template("admin/list.html", title="Students", columns=["Student ID", "Name", "Class", "Roll", "Gender", "Parent", "Status"],
                           rows=rows, page=page, pages=pages, total=total,
                           filters=[{"name": "class_id", "label": "Class", "options": class_ids},
                                    {"name": "q", "label": "Search name or ID", "text": True}])


@bp.route("/teachers")
@role_required("Admin")
def teachers():
    q = request.args.get("q", "").strip()
    flt = {"$or": [{"name": regex(q)}, {"teacherId": regex(q)}]} if q else {}
    items, page, pages, total = access.paginate("teachers", flt, [("teacherId", 1)], request.args.get("page", 1, type=int))
    leads, teaches = {}, {}
    for c in col("classes").find():
        leads.setdefault(c["classteacherid"], []).append("%s%s" % (c["classname"], c["section"]))
    for s in col("subjects").find():
        teaches.setdefault(s["teacherId"], []).append("%s (Class %s)" % (s["subjectName"], s["className"]))
    rows = [[t["teacherId"], t["name"], t["designation"], t["email"], t["phone"], ", ".join(leads.get(t["teacherId"], [])) or "-",
             ", ".join(teaches.get(t["teacherId"], [])) or "-", badge(t["status"])] for t in items]
    return render_template("admin/list.html", title="Teachers", rows=rows, page=page, pages=pages, total=total,
                           columns=["Teacher ID", "Name", "Designation", "Email", "Phone", "Class teacher of", "Teaches", "Status"],
                           filters=[{"name": "q", "label": "Search name or ID", "text": True}])


@bp.route("/classes")
@role_required("Admin")
def classes():
    per_class = Counter(s["classId"] for s in col("students").find({}, {"classId": 1}))
    teachers = {t["teacherId"]: t["name"] for t in col("teachers").find()}
    items = list(col("classes").find().sort("classid", 1))
    rows = [[c["classid"], "Class %s" % c["classname"], c["section"], teachers.get(c["classteacherid"], c["classteacherid"]),
             per_class.get(c["classid"], 0), c["studentcapacity"], c["academicyear"]] for c in items]
    return render_template("admin/list.html", title="Classes", rows=rows, page=1, pages=1, total=len(rows), filters=[],
                           columns=["Class ID", "Grade", "Section", "Class teacher", "Students", "Capacity", "Academic year"])


@bp.route("/notifications")
@role_required("Admin")
def notifications():
    audience, priority = request.args.get("audience", ""), request.args.get("priority", "")
    flt = {}
    if audience in ("All", "Parents", "Students", "Teachers", "Class"):
        flt["targetAudience"] = audience
    if priority in ("High", "Normal", "Low"):
        flt["priority"] = priority
    items, page, pages, total = access.paginate("notifications", flt, [("createdDate", -1), ("notificationId", -1)],
                                                request.args.get("page", 1, type=int))
    creators = {u["userId"]: u["name"] for u in col("users").find({"userId": {"$in": [n["createdBy"] for n in items]}}, access.NO_PASSWORD)}
    rows = [[n["notificationId"], n["title"], n["targetAudience"] + (" (%s)" % n["classId"] if n["targetAudience"] == "Class" else ""),
             badge(n["priority"]), creators.get(n["createdBy"], n["createdBy"]), n["createdDate"].strftime("%d %b %Y")] for n in items]
    return render_template("admin/list.html", title="Notifications", rows=rows, page=page, pages=pages, total=total,
                           columns=["ID", "Title", "Audience", "Priority", "Created by", "Date"],
                           filters=[{"name": "audience", "label": "Audience", "options": ["All", "Parents", "Students", "Teachers", "Class"]},
                                    {"name": "priority", "label": "Priority", "options": ["High", "Normal", "Low"]}])
