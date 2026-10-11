```python
"""Admin pages (read-only):
dashboard, users, students, teachers, classes,
marks, attendance, notifications.
"""

import re
from collections import Counter

from flask import Blueprint, render_template, request

import access
from auth import role_required
from db import col

bp = Blueprint("admin", __name__, url_prefix="/admin")

BADGE = {
    "Active": "success",
    "Inactive": "secondary",
    "High": "danger",
    "Normal": "primary",
    "Low": "secondary",
    "Admin": "dark",
    "Teacher": "info",
    "Parent": "success",
    "Student": "secondary",
}


def badge(text):
    return {
        "text": text,
        "badge": BADGE.get(text, "secondary")
    }


def regex(text):
    return {
        "$regex": re.escape(text),
        "$options": "i"
    }


def safe_value(value):
    """Convert missing or unsupported values into displayable text."""
    if value is None:
        return "-"
    return str(value)


def format_date(value):
    """Format a date when the stored value supports it."""
    if value is None:
        return "-"
    if hasattr(value, "strftime"):
        return value.strftime("%d %b %Y")
    return str(value)


@bp.route("/")
@role_required("Admin")
def dashboard():
    # Count users by role
    roles = {
        role: col("users").count_documents({"role": role})
        for role in ("Admin", "Teacher", "Parent", "Student")
    }

    # Count students in each class
    per_class = Counter(
        student["classId"]
        for student in col("students").find(
            {},
            {"classId": 1}
        )
        if "classId" in student
    )

    # Get teacher names
    teachers_map = {
        teacher["teacherId"]: teacher["name"]
        for teacher in col("teachers").find(
            {},
            {"teacherId": 1, "name": 1}
        )
        if "teacherId" in teacher and "name" in teacher
    }

    # Prepare class information
    class_items = [
        {
            "c": class_item,
            "students": per_class.get(
                class_item.get("classid"), 0
            ),
            "teacher": teachers_map.get(
                class_item.get("classteacherid"), "-"
            ),
        }
        for class_item in col("classes").find().sort(
            "classid", 1
        )
    ]

    # Dashboard statistics
    stats = {
        "Students": col("students").count_documents({}),
        "Teachers": col("teachers").count_documents({}),
        "Parents": col("parents").count_documents({}),
        "Classes": col("classes").count_documents({}),
        "Messages": col("messages").count_documents({}),
        "Unread messages": col("messages").count_documents(
            {"status": "Sent"}
        ),
        "Marks records": col("marks").count_documents({}),
        "Attendance records": col("attendance").count_documents({}),
    }

    # Latest notifications
    recent = list(
        col("notifications")
        .find()
        .sort([
            ("createdDate", -1),
            ("notificationId", -1)
        ])
        .limit(5)
    )

    return render_template(
        "admin/dashboard.html",
        stats=stats,
        roles=roles,
        classes=class_items,
        recent=recent
    )


@bp.route("/users")
@role_required("Admin")
def users():
    role = request.args.get("role", "")
    query = request.args.get("q", "").strip()

    filters = {}

    if role in ("Admin", "Teacher", "Parent", "Student"):
        filters["role"] = role

    if query:
        filters["$or"] = [
            {"name": regex(query)},
            {"email": regex(query)},
            {"userId": regex(query)}
        ]

    items, page, pages, total = access.paginate(
        "users",
        filters,
        [("userId", 1)],
        request.args.get("page", 1, type=int),
        projection=access.NO_PASSWORD
    )

    rows = [
        [
            user["userId"],
            user["name"],
            user["email"],
            badge(user["role"]),
            user.get("referenceId", ""),
            badge(user["status"])
        ]
        for user in items
    ]

    return render_template(
        "admin/list.html",
        title="Users",
        columns=[
            "User ID", "Name", "Email", "Role",
            "Linked record", "Status"
        ],
        rows=rows,
        page=page,
        pages=pages,
        total=total,
        filters=[
            {
                "name": "role",
                "label": "Role",
                "options": [
                    "Admin", "Teacher", "Parent", "Student"
                ]
            },
            {
                "name": "q",
                "label": "Search name, email or ID",
                "text": True
            }
        ]
    )


@bp.route("/students")
@role_required("Admin")
def students():
    class_id = request.args.get("class_id", "")
    query = request.args.get("q", "").strip()

    filters = {}

    if class_id:
        filters["classId"] = class_id

    if query:
        filters["$or"] = [
            {"name": regex(query)},
            {"studentId": regex(query)}
        ]

    items, page, pages, total = access.paginate(
        "students",
        filters,
        [("classId", 1), ("rollNumber", 1)],
        request.args.get("page", 1, type=int)
    )

    parent_ids = [
        student["parentId"]
        for student in items
        if "parentId" in student
    ]

    parents_map = {
        parent["parentId"]: parent["name"]
        for parent in col("parents").find(
            {"parentId": {"$in": parent_ids}}
        )
    }

    rows = [
        [
            student.get("studentId", "-"),
            student.get("name", "-"),
            "{}{}".format(
                student.get("className", ""),
                student.get("section", "")
            ),
            student.get("rollNumber", "-"),
            student.get("gender", "-"),
            parents_map.get(student.get("parentId"), "-"),
            badge(student.get("status", "Inactive"))
        ]
        for student in items
    ]

    class_ids = [
        class_item["classid"]
        for class_item in col("classes").find().sort(
            "classid", 1
        )
    ]

    return render_template(
        "admin/list.html",
        title="Students",
        columns=[
            "Student ID", "Name", "Class", "Roll",
            "Gender", "Parent", "Status"
        ],
        rows=rows,
        page=page,
        pages=pages,
        total=total,
        filters=[
            {
                "name": "class_id",
                "label": "Class",
                "options": class_ids
            },
            {
                "name": "q",
                "label": "Search name or ID",
                "text": True
            }
        ]
    )


@bp.route("/teachers")
@role_required("Admin")
def teachers():
    query = request.args.get("q", "").strip()

    filters = {
        "$or": [
            {"name": regex(query)},
            {"teacherId": regex(query)}
        ]
    } if query else {}

    items, page, pages, total = access.paginate(
        "teachers",
        filters,
        [("teacherId", 1)],
        request.args.get("page", 1, type=int)
    )

    class_leads = {}
    subjects_taught = {}

    for class_item in col("classes").find():
        teacher_id = class_item.get("classteacherid")

        if teacher_id:
            class_leads.setdefault(
                teacher_id, []
            ).append(
                "{}{}".format(
                    class_item.get("classname", ""),
                    class_item.get("section", "")
                )
            )

    for subject in col("subjects").find():
        teacher_id = subject.get("teacherId")

        if teacher_id:
            subjects_taught.setdefault(
                teacher_id, []
            ).append(
                "{} (Class {})".format(
                    subject.get("subjectName", "-"),
                    subject.get("className", "-")
                )
            )

    rows = [
        [
            teacher.get("teacherId", "-"),
            teacher.get("name", "-"),
            teacher.get("designation", "-"),
            teacher.get("email", "-"),
            teacher.get("phone", "-"),
            ", ".join(
                class_leads.get(
                    teacher.get("teacherId"), []
                )
            ) or "-",
            ", ".join(
                subjects_taught.get(
                    teacher.get("teacherId"), []
                )
            ) or "-",
            badge(teacher.get("status", "Inactive"))
        ]
        for teacher in items
    ]

    return render_template(
        "admin/list.html",
        title="Teachers",
        rows=rows,
        page=page,
        pages=pages,
        total=total,
        columns=[
            "Teacher ID", "Name", "Designation", "Email",
            "Phone", "Class teacher of", "Teaches", "Status"
        ],
        filters=[
            {
                "name": "q",
                "label": "Search name or ID",
                "text": True
            }
        ]
    )


@bp.route("/classes")
@role_required("Admin")
def classes():
    per_class = Counter(
        student["classId"]
        for student in col("students").find(
            {}, {"classId": 1}
        )
        if "classId" in student
    )

    teachers_map = {
        teacher["teacherId"]: teacher["name"]
        for teacher in col("teachers").find()
        if "teacherId" in teacher and "name" in teacher
    }

    items = list(
        col("classes").find().sort("classid", 1)
    )

    rows = [
        [
            class_item.get("classid", "-"),
            "Class {}".format(
                class_item.get("classname", "-")
            ),
            class_item.get("section", "-"),
            teachers_map.get(
                class_item.get("classteacherid"),
                class_item.get("classteacherid", "-")
            ),
            per_class.get(class_item.get("classid"), 0),
            class_item.get("studentcapacity", "-"),
            class_item.get("academicyear", "-")
        ]
        for class_item in items
    ]

    return render_template(
        "admin/list.html",
        title="Classes",
        rows=rows,
        page=1,
        pages=1,
        total=len(rows),
        filters=[],
        columns=[
            "Class ID", "Grade", "Section", "Class teacher",
            "Students", "Capacity", "Academic year"
        ]
    )


@bp.route("/marks")
@role_required("Admin")
def marks():
    """Read-only view of existing marks records."""
    query = request.args.get("q", "").strip()

    filters = {}

    if query:
        search_fields = [
            "studentId",
            "studentName",
            "subjectId",
            "subjectName",
            "classId",
            "examType"
        ]

        filters["$or"] = [
            {field: regex(query)}
            for field in search_fields
        ]

    items, page, pages, total = access.paginate(
        "marks",
        filters,
        [("studentId", 1)],
        request.args.get("page", 1, type=int)
    )

    rows = []

    for item in items:
        rows.append([
            safe_value(item.get("studentId")),
            safe_value(item.get("studentName")),
            safe_value(item.get("classId")),
            safe_value(item.get("subjectId")),
            safe_value(item.get("subjectName")),
            safe_value(item.get("examType")),
            safe_value(item.get("marksObtained")),
            safe_value(item.get("totalMarks")),
            safe_value(item.get("grade"))
        ])

    return render_template(
        "admin/list.html",
        title="Marks Records",
        columns=[
            "Student ID", "Student Name", "Class",
            "Subject ID", "Subject", "Exam",
            "Marks Obtained", "Total Marks", "Grade"
        ],
        rows=rows,
        page=page,
        pages=pages,
        total=total,
        filters=[
            {
                "name": "q",
                "label": "Search student, subject or class",
                "text": True
            }
        ]
    )


@bp.route("/attendance")
@role_required("Admin")
def attendance():
    """Read-only view of existing attendance records."""
    query = request.args.get("q", "").strip()

    filters = {}

    if query:
        search_fields = [
            "studentId",
            "studentName",
            "classId",
            "subjectId",
            "status"
        ]

        filters["$or"] = [
            {field: regex(query)}
            for field in search_fields
        ]

    items, page, pages, total = access.paginate(
        "attendance",
        filters,
        [("studentId", 1)],
        request.args.get("page", 1, type=int)
    )

    rows = []

    for item in items:
        rows.append([
            safe_value(item.get("studentId")),
            safe_value(item.get("studentName")),
            safe_value(item.get("classId")),
            safe_value(item.get("subjectId")),
            format_date(
                item.get("attendanceDate")
                or item.get("date")
                or item.get("createdDate")
            ),
            safe_value(item.get("status"))
        ])

    return render_template(
        "admin/list.html",
        title="Attendance Records",
        columns=[
            "Student ID", "Student Name", "Class",
            "Subject ID", "Date", "Status"
        ],
        rows=rows,
        page=page,
        pages=pages,
        total=total,
        filters=[
            {
                "name": "q",
                "label": "Search student, class or status",
                "text": True
            }
        ]
    )


@bp.route("/notifications")
@role_required("Admin")
def notifications():
    audience = request.args.get("audience", "")
    priority = request.args.get("priority", "")

    filters = {}

    if audience in (
        "All", "Parents", "Students", "Teachers", "Class"
    ):
        filters["targetAudience"] = audience

    if priority in ("High", "Normal", "Low"):
        filters["priority"] = priority

    items, page, pages, total = access.paginate(
        "notifications",
        filters,
        [("createdDate", -1), ("notificationId", -1)],
        request.args.get("page", 1, type=int)
    )

    creator_ids = [
        notification["createdBy"]
        for notification in items
        if "createdBy" in notification
    ]

    creators = {
        user["userId"]: user["name"]
        for user in col("users").find(
            {"userId": {"$in": creator_ids}},
            access.NO_PASSWORD
        )
        if "userId" in user and "name" in user
    }

    rows = [
        [
            notification.get("notificationId", "-"),
            notification.get("title", "-"),
            notification.get("targetAudience", "-")
            + (
                " ({})".format(notification.get("classId", "-"))
                if notification.get("targetAudience") == "Class"
                else ""
            ),
            badge(notification.get("priority", "Normal")),
            creators.get(
                notification.get("createdBy"),
                notification.get("createdBy", "-")
            ),
            format_date(notification.get("createdDate"))
        ]
        for notification in items
    ]

    return render_template(
        "admin/list.html",
        title="Notifications",
        rows=rows,
        page=page,
        pages=pages,
        total=total,
        columns=[
            "ID", "Title", "Audience", "Priority",
            "Created by", "Date"
        ],
        filters=[
            {
                "name": "audience",
                "label": "Audience",
                "options": [
                    "All", "Parents", "Students",
                    "Teachers", "Class"
                ]
            },
            {
                "name": "priority",
                "label": "Priority",
                "options": ["High", "Normal", "Low"]
            }
        ]
    )
```