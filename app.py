"""
Student-Parent Communication Platform
--------------------------------------
Simple Flask + SQLite full-stack application.

Run with:  python app.py
Then open: http://127.0.0.1:5000
"""

from functools import wraps
import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash

import database as db

app = Flask(__name__)
app.secret_key = "college-project-secret-key-change-if-needed"  # fine for a demo project

# Make sure the database exists and has sample data every time the app starts
db.init_db()
db.seed_data()


# ---------------------------------------------------------------------------
# Helpers / decorators
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if session.get("role") not in roles:
                flash("You do not have access to that page.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def current_user():
    if "user_id" not in session:
        return None
    conn = db.get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    conn.close()
    return user


def get_student_details(student_user_id):
    """
    Full Student -> Parent -> Teacher relationship record for one student,
    looked up by the student's user_id. Returns a single row (or None) with
    the student's own info plus their linked parent's and teacher's names.
    """
    conn = db.get_connection()
    row = conn.execute(
        """
        SELECT s.roll_no, s.class_name, s.section, s.academic_year, s.date_of_birth,
               su.full_name AS student_name, su.username AS student_username, su.email AS student_email,
               pu.full_name AS parent_name, pu.username AS parent_username, pu.email AS parent_email,
               tu.full_name AS teacher_name
        FROM students s
        JOIN users su ON su.id = s.user_id
        LEFT JOIN users pu ON pu.id = s.parent_id
        LEFT JOIN users tu ON tu.id = s.teacher_id
        WHERE s.user_id = ?
        """,
        (student_user_id,),
    ).fetchone()
    conn.close()
    return row


def get_notifications_for_role(role, limit=5):
    """Latest notifications visible to a given role (or everyone)."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT title, message, created_at FROM notifications "
        "WHERE target_role = 'all' OR target_role = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (role, limit),
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Attendance module helpers
# ---------------------------------------------------------------------------
# These reuse the existing "attendance" table exactly as it already exists in
# database.py (student_id, date, status, marked_by, UNIQUE(student_id, date)).
# No schema changes were needed for this module.

def get_teacher_classes(teacher_user_id):
    """Distinct classes this teacher actually has students in."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT DISTINCT class_name, COALESCE(section, '') AS section "
        "FROM students WHERE teacher_id = ? ORDER BY class_name, section",
        (teacher_user_id,),
    ).fetchall()
    conn.close()
    return rows


def get_class_students(teacher_user_id, class_name, section):
    """
    Authoritative list of students in one class for one teacher, straight from
    the database. Always recomputed server-side before saving attendance, so
    a teacher can only ever mark attendance for students actually assigned to
    them - form data for any other student id is ignored.
    """
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT s.id AS student_id, s.roll_no, u.full_name "
        "FROM students s JOIN users u ON u.id = s.user_id "
        "WHERE s.teacher_id = ? AND s.class_name = ? AND COALESCE(s.section, '') = ? "
        "ORDER BY s.roll_no",
        (teacher_user_id, class_name, section),
    ).fetchall()
    conn.close()
    return rows


def get_attendance_map(student_ids, date_str):
    """{student_id: status} for one date, used to pre-fill the marking form."""
    if not student_ids:
        return {}
    conn = db.get_connection()
    placeholders = ",".join("?" for _ in student_ids)
    rows = conn.execute(
        f"SELECT student_id, status FROM attendance WHERE date = ? AND student_id IN ({placeholders})",
        [date_str] + list(student_ids),
    ).fetchall()
    conn.close()
    return {r["student_id"]: r["status"] for r in rows}


def get_attendance_summary(student_pk):
    """
    Attendance summary + full record list for one student, looked up by the
    student's internal students.id (never exposed in any URL).
    """
    conn = db.get_connection()
    counts = conn.execute(
        "SELECT status, COUNT(*) c FROM attendance WHERE student_id = ? GROUP BY status",
        (student_pk,),
    ).fetchall()
    records = conn.execute(
        "SELECT date, status FROM attendance WHERE student_id = ? ORDER BY date DESC",
        (student_pk,),
    ).fetchall()
    conn.close()

    total = sum(r["c"] for r in counts)
    present = sum(r["c"] for r in counts if r["status"] == "Present")
    absent = total - present  # anything not "Present" counts against attendance
    percentage = round((present / total) * 100, 1) if total else None

    return {
        "total": total,
        "present": present,
        "absent": absent,
        "percentage": percentage,
        "records": records,
    }


# ---------------------------------------------------------------------------
# Marks module helpers
# ---------------------------------------------------------------------------
# Reuses the existing "marks" table (student_id, subject, exam_name,
# marks_obtained, max_marks, entered_by, entered_at). Only one column
# (exam_date) and one unique index were added in database.py, non-destructively.

def get_marks_summary(student_pk):
    """All marks rows + overall percentage for one student, by students.id."""
    conn = db.get_connection()
    records = conn.execute(
        "SELECT subject, exam_name, marks_obtained, max_marks, exam_date "
        "FROM marks WHERE student_id = ? ORDER BY exam_date DESC, id DESC",
        (student_pk,),
    ).fetchall()
    conn.close()

    total_obtained = sum(r["marks_obtained"] for r in records)
    total_max = sum(r["max_marks"] for r in records)
    overall_percentage = round((total_obtained / total_max) * 100, 1) if total_max else None

    return {"records": records, "overall_percentage": overall_percentage}


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")

        if not identifier or not password:
            flash("Please enter both username/email and password.", "danger")
            return render_template("login.html")

        conn = db.get_connection()
        # Allow logging in with either the username or the registered email
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?", (identifier, identifier)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            flash(f"Welcome back, {user['full_name']}!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username/email or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboards - one protected route per role, plus a redirector
# ---------------------------------------------------------------------------
# Each dashboard route is locked to its own role with @role_required(...).
# /dashboard simply looks at the logged-in user's role and sends them to the
# correct one. If a logged-in user tries to open a dashboard URL for a role
# that is not theirs (e.g. a student opening /dashboard/admin), role_required
# blocks it and redirects them back to their own dashboard with an error.

ROLE_DASHBOARD_ENDPOINT = {
    "admin": "dashboard_admin",
    "teacher": "dashboard_teacher",
    "student": "dashboard_student",
    "parent": "dashboard_parent",
}


@app.route("/dashboard")
@login_required
def dashboard():
    role = session.get("role")
    endpoint = ROLE_DASHBOARD_ENDPOINT.get(role)
    if not endpoint:
        flash("Unknown role.", "danger")
        return redirect(url_for("login"))
    return redirect(url_for(endpoint))


@app.route("/dashboard/admin")
@role_required("admin")
def dashboard_admin():
    user = current_user()
    conn = db.get_connection()
    stats = {
        "total_students": conn.execute("SELECT COUNT(*) c FROM users WHERE role='student'").fetchone()["c"],
        "total_teachers": conn.execute("SELECT COUNT(*) c FROM users WHERE role='teacher'").fetchone()["c"],
        "total_parents": conn.execute("SELECT COUNT(*) c FROM users WHERE role='parent'").fetchone()["c"],
        "total_notifications": conn.execute("SELECT COUNT(*) c FROM notifications").fetchone()["c"],
    }
    conn.close()
    return render_template("dashboard_admin.html", user=user, stats=stats)


@app.route("/dashboard/teacher")
@role_required("teacher")
def dashboard_teacher():
    user = current_user()
    conn = db.get_connection()
    student_count = conn.execute(
        "SELECT COUNT(*) c FROM students WHERE teacher_id = ?", (user["id"],)
    ).fetchone()["c"]
    conn.close()
    return render_template("dashboard_teacher.html", user=user, student_count=student_count)


@app.route("/dashboard/student")
@role_required("student")
def dashboard_student():
    user = current_user()
    details = get_student_details(user["id"])
    notifications = get_notifications_for_role("student")

    # Small additive lookup for the dashboard's attendance summary card.
    # Does not touch get_student_details() or the profile page logic.
    conn = db.get_connection()
    student_pk_row = conn.execute("SELECT id FROM students WHERE user_id = ?", (user["id"],)).fetchone()
    conn.close()
    attendance_summary = get_attendance_summary(student_pk_row["id"]) if student_pk_row else None
    marks_summary = get_marks_summary(student_pk_row["id"]) if student_pk_row else None

    return render_template(
        "dashboard_student.html",
        user=user,
        details=details,
        notifications=notifications,
        attendance_summary=attendance_summary,
        marks_summary=marks_summary,
    )


@app.route("/dashboard/parent")
@role_required("parent")
def dashboard_parent():
    user = current_user()
    conn = db.get_connection()
    child_link = conn.execute(
        "SELECT id, user_id FROM students WHERE parent_id = ?", (user["id"],)
    ).fetchone()
    conn.close()

    details = get_student_details(child_link["user_id"]) if child_link else None
    notifications = get_notifications_for_role("parent")
    attendance_summary = get_attendance_summary(child_link["id"]) if child_link else None
    marks_summary = get_marks_summary(child_link["id"]) if child_link else None

    return render_template(
        "dashboard_parent.html",
        user=user,
        details=details,
        notifications=notifications,
        attendance_summary=attendance_summary,
        marks_summary=marks_summary,
    )


# ---------------------------------------------------------------------------
# Student-Parent Core Module: profile pages
# ---------------------------------------------------------------------------
# Neither route accepts an ID in the URL - each one always looks up data for
# the CURRENTLY LOGGED-IN user's own session. That is what stops a student
# from viewing another student's profile, and a parent from viewing another
# parent's child: there is no URL parameter to tamper with.

@app.route("/profile")
@role_required("student")
def profile():
    user = current_user()
    details = get_student_details(user["id"])
    return render_template("profile_student.html", user=user, details=details)


@app.route("/profile/child")
@role_required("parent")
def child_profile():
    user = current_user()
    conn = db.get_connection()
    child_link = conn.execute(
        "SELECT user_id FROM students WHERE parent_id = ?", (user["id"],)
    ).fetchone()
    conn.close()

    if not child_link:
        flash("No child is currently linked to your account.", "warning")
        return render_template("profile_child.html", user=user, details=None)

    details = get_student_details(child_link["user_id"])
    return render_template("profile_child.html", user=user, details=details)


# ---------------------------------------------------------------------------
# Attendance module
# ---------------------------------------------------------------------------
# Same security pattern as the profile pages above: none of these routes take
# a student id from the URL. The teacher route only ever operates on students
# recomputed server-side for that teacher's own class; the student and parent
# routes only ever look up the current session's own record / linked child.

@app.route("/attendance/teacher", methods=["GET", "POST"])
@role_required("teacher")
def attendance_teacher():
    user = current_user()
    classes = get_teacher_classes(user["id"])

    class_key = request.values.get("class_key", "").strip()
    date_str = request.values.get("date", "").strip() or datetime.date.today().isoformat()

    selected_class_name, selected_section = None, None
    if "||" in class_key:
        selected_class_name, selected_section = class_key.split("||", 1)

    if request.method == "POST":
        if not selected_class_name:
            flash("Please select a class before saving.", "danger")
            return redirect(url_for("attendance_teacher"))

        # Authoritative list, straight from the DB for THIS teacher's class -
        # ignores any tampering with the submitted form.
        class_students = get_class_students(user["id"], selected_class_name, selected_section)
        if not class_students:
            flash("You are not assigned to that class.", "danger")
            return redirect(url_for("attendance_teacher"))

        conn = db.get_connection()
        saved, skipped = 0, []
        for s in class_students:
            status = request.form.get(f"status_{s['student_id']}")
            if status not in ("Present", "Absent"):
                skipped.append(s["roll_no"])
                continue
            conn.execute(
                "INSERT INTO attendance (student_id, date, status, marked_by) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(student_id, date) DO UPDATE SET status = excluded.status, marked_by = excluded.marked_by",
                (s["student_id"], date_str, status, user["id"]),
            )
            saved += 1
        conn.commit()
        conn.close()

        if saved:
            flash(f"Attendance saved for {saved} student(s) on {date_str}.", "success")
        if skipped:
            flash(f"No status selected for: {', '.join(skipped)} (not saved).", "warning")

        return redirect(url_for("attendance_teacher", class_key=class_key, date=date_str))

    # GET
    class_students = []
    attendance_map = {}
    if selected_class_name:
        class_students = get_class_students(user["id"], selected_class_name, selected_section)
        attendance_map = get_attendance_map([s["student_id"] for s in class_students], date_str)
        if not class_students:
            flash("You are not assigned to that class.", "danger")

    return render_template(
        "attendance_teacher.html",
        user=user,
        classes=classes,
        class_key=class_key,
        date=date_str,
        students=class_students,
        attendance_map=attendance_map,
    )


@app.route("/attendance/student")
@role_required("student")
def attendance_student():
    user = current_user()
    conn = db.get_connection()
    student_row = conn.execute(
        "SELECT id, roll_no, class_name, section FROM students WHERE user_id = ?", (user["id"],)
    ).fetchone()
    conn.close()

    if not student_row:
        flash("No student record is linked to your account yet.", "warning")
        return render_template("attendance_student.html", user=user, student=None, summary=None)

    summary = get_attendance_summary(student_row["id"])
    return render_template("attendance_student.html", user=user, student=student_row, summary=summary)


@app.route("/attendance/parent")
@role_required("parent")
def attendance_parent():
    user = current_user()
    conn = db.get_connection()
    child = conn.execute(
        "SELECT s.id, s.roll_no, s.class_name, s.section, u.full_name AS student_name "
        "FROM students s JOIN users u ON u.id = s.user_id WHERE s.parent_id = ?",
        (user["id"],),
    ).fetchone()
    conn.close()

    if not child:
        flash("No child is currently linked to your account.", "warning")
        return render_template("attendance_parent.html", user=user, child=None, summary=None)

    summary = get_attendance_summary(child["id"])
    return render_template("attendance_parent.html", user=user, child=child, summary=summary)


# ---------------------------------------------------------------------------
# Marks module
# ---------------------------------------------------------------------------
# Same security pattern as Attendance and Profile: the teacher route always
# recomputes the authoritative student list for that teacher's own class
# server-side before saving; the student and parent routes only ever look up
# the current session's own record / linked child - no student id in any URL.

@app.route("/marks/teacher", methods=["GET", "POST"])
@role_required("teacher")
def marks_teacher():
    user = current_user()
    classes = get_teacher_classes(user["id"])

    class_key = request.values.get("class_key", "").strip()
    selected_class_name, selected_section = None, None
    if "||" in class_key:
        selected_class_name, selected_section = class_key.split("||", 1)

    class_students = []
    if selected_class_name:
        class_students = get_class_students(user["id"], selected_class_name, selected_section)
        if not class_students:
            flash("You are not assigned to that class.", "danger")

    if request.method == "POST":
        student_id_raw = request.form.get("student_id", "")
        subject = request.form.get("subject", "").strip()
        exam_name = request.form.get("exam_name", "").strip()
        max_marks_raw = request.form.get("max_marks", "").strip()
        obtained_marks_raw = request.form.get("obtained_marks", "").strip()
        exam_date = request.form.get("exam_date", "").strip() or datetime.date.today().isoformat()

        if not selected_class_name:
            flash("Please select a class before saving.", "danger")
            return redirect(url_for("marks_teacher"))

        # Authoritative check: the chosen student must actually be in THIS
        # teacher's class list, recomputed from the DB - never trust the form.
        valid_student_ids = {str(s["student_id"]) for s in class_students}
        if student_id_raw not in valid_student_ids:
            flash("You are not authorized to enter marks for that student.", "danger")
            return redirect(url_for("marks_teacher", class_key=class_key))

        if not subject or not exam_name:
            flash("Subject and exam/assessment name are required.", "danger")
            return redirect(url_for("marks_teacher", class_key=class_key, student_id=student_id_raw))

        try:
            max_marks = float(max_marks_raw)
            obtained_marks = float(obtained_marks_raw)
        except ValueError:
            flash("Maximum marks and obtained marks must be numbers.", "danger")
            return redirect(url_for("marks_teacher", class_key=class_key, student_id=student_id_raw))

        if max_marks <= 0 or obtained_marks < 0 or obtained_marks > max_marks:
            flash("Obtained marks must be between 0 and the maximum marks.", "danger")
            return redirect(url_for("marks_teacher", class_key=class_key, student_id=student_id_raw))

        conn = db.get_connection()
        conn.execute(
            "INSERT INTO marks (student_id, subject, exam_name, marks_obtained, max_marks, exam_date, entered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_id, subject, exam_name) DO UPDATE SET "
            "marks_obtained = excluded.marks_obtained, max_marks = excluded.max_marks, "
            "exam_date = excluded.exam_date, entered_by = excluded.entered_by",
            (int(student_id_raw), subject, exam_name, obtained_marks, max_marks, exam_date, user["id"]),
        )
        conn.commit()
        conn.close()

        flash(f"Marks saved for {subject} - {exam_name}.", "success")
        return redirect(url_for("marks_teacher", class_key=class_key, student_id=student_id_raw))

    # GET
    selected_student = None
    student_marks = None
    student_id_param = request.args.get("student_id", "")
    if student_id_param and student_id_param.isdigit():
        for s in class_students:
            if str(s["student_id"]) == student_id_param:
                selected_student = s
                break
        if selected_student:
            student_marks = get_marks_summary(selected_student["student_id"])

    return render_template(
        "marks_teacher.html",
        user=user,
        classes=classes,
        class_key=class_key,
        students=class_students,
        selected_student=selected_student,
        student_marks=student_marks,
        today=datetime.date.today().isoformat(),
    )


@app.route("/marks/student")
@role_required("student")
def marks_student():
    user = current_user()
    conn = db.get_connection()
    student_row = conn.execute(
        "SELECT id, roll_no, class_name, section FROM students WHERE user_id = ?", (user["id"],)
    ).fetchone()
    conn.close()

    if not student_row:
        flash("No student record is linked to your account yet.", "warning")
        return render_template("marks_student.html", user=user, student=None, summary=None)

    summary = get_marks_summary(student_row["id"])
    return render_template("marks_student.html", user=user, student=student_row, summary=summary)


@app.route("/marks/parent")
@role_required("parent")
def marks_parent():
    user = current_user()
    conn = db.get_connection()
    child = conn.execute(
        "SELECT s.id, s.roll_no, s.class_name, s.section, u.full_name AS student_name "
        "FROM students s JOIN users u ON u.id = s.user_id WHERE s.parent_id = ?",
        (user["id"],),
    ).fetchone()
    conn.close()

    if not child:
        flash("No child is currently linked to your account.", "warning")
        return render_template("marks_parent.html", user=user, child=None, summary=None)

    summary = get_marks_summary(child["id"])
    return render_template("marks_parent.html", user=user, child=child, summary=summary)


# ---------------------------------------------------------------------------
# Parent <-> Teacher Messaging module
# ---------------------------------------------------------------------------
# Reuses the existing "messages" table exactly as it already exists in
# database.py (sender_id, receiver_id, content, sent_at, is_read). No schema
# changes were needed. Same security pattern as the modules above: the parent
# route only ever resolves the teacher through the parent's own linked child
# (no id taken from the URL/form); the teacher route accepts a parent_id, but
# it is always checked against the authoritative list of parents linked to
# that teacher's own students before anything is read or written.

def get_parents_for_teacher(teacher_user_id):
    """Distinct parents linked (via a student) to this teacher, for authorization + listing."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT DISTINCT pu.id AS parent_id, pu.full_name AS parent_name, "
        "su.full_name AS student_name "
        "FROM students s "
        "JOIN users pu ON pu.id = s.parent_id "
        "JOIN users su ON su.id = s.user_id "
        "WHERE s.teacher_id = ? "
        "ORDER BY pu.full_name",
        (teacher_user_id,),
    ).fetchall()
    conn.close()
    return rows


def get_conversation(user_a_id, user_b_id):
    """All messages between two specific users, oldest first."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT sender_id, receiver_id, content, sent_at FROM messages "
        "WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?) "
        "ORDER BY sent_at ASC, id ASC",
        (user_a_id, user_b_id, user_b_id, user_a_id),
    ).fetchall()
    conn.close()
    return rows


def send_message(sender_id, receiver_id, content):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
        (sender_id, receiver_id, content),
    )
    conn.commit()
    conn.close()


@app.route("/messages/parent", methods=["GET", "POST"])
@role_required("parent")
def messages_parent():
    user = current_user()
    conn = db.get_connection()
    teacher_row = conn.execute(
        "SELECT s.teacher_id, tu.full_name AS teacher_name "
        "FROM students s LEFT JOIN users tu ON tu.id = s.teacher_id "
        "WHERE s.parent_id = ?",
        (user["id"],),
    ).fetchone()
    conn.close()

    teacher_id = teacher_row["teacher_id"] if teacher_row else None

    if request.method == "POST":
        if not teacher_id:
            flash("No teacher is linked to your child yet.", "danger")
            return redirect(url_for("messages_parent"))
        content = request.form.get("content", "").strip()
        if not content:
            flash("Message cannot be empty.", "danger")
            return redirect(url_for("messages_parent"))
        send_message(user["id"], teacher_id, content)
        return redirect(url_for("messages_parent"))

    conversation = get_conversation(user["id"], teacher_id) if teacher_id else []
    return render_template(
        "messages_parent.html",
        user=user,
        teacher_name=teacher_row["teacher_name"] if teacher_row else None,
        conversation=conversation,
    )


@app.route("/messages/teacher", methods=["GET", "POST"])
@role_required("teacher")
def messages_teacher():
    user = current_user()
    parents = get_parents_for_teacher(user["id"])
    valid_parent_ids = {str(p["parent_id"]) for p in parents}

    if request.method == "POST":
        parent_id_raw = request.form.get("parent_id", "")
        content = request.form.get("content", "").strip()

        if parent_id_raw not in valid_parent_ids:
            flash("You are not authorized to message that parent.", "danger")
            return redirect(url_for("messages_teacher"))
        if not content:
            flash("Message cannot be empty.", "danger")
            return redirect(url_for("messages_teacher", parent_id=parent_id_raw))

        send_message(user["id"], int(parent_id_raw), content)
        return redirect(url_for("messages_teacher", parent_id=parent_id_raw))

    # GET
    parent_id_param = request.args.get("parent_id", "")
    selected_parent = None
    conversation = []
    if parent_id_param in valid_parent_ids:
        for p in parents:
            if str(p["parent_id"]) == parent_id_param:
                selected_parent = p
                break
        conversation = get_conversation(user["id"], int(parent_id_param))
    elif parent_id_param:
        flash("You are not authorized to message that parent.", "danger")

    return render_template(
        "messages_teacher.html",
        user=user,
        parents=parents,
        selected_parent=selected_parent,
        conversation=conversation,
    )


# ---------------------------------------------------------------------------
# Basic Analytics module
# ---------------------------------------------------------------------------
# Read-only aggregate queries over the existing "attendance" and "marks"
# tables - no schema changes, no new tables. Admin sees school-wide figures;
# Teacher sees the same figures scoped to only their own assigned students
# (filtered server-side via students.teacher_id, never from a URL/form value).

def get_admin_attendance_analytics():
    conn = db.get_connection()
    total_students = conn.execute("SELECT COUNT(*) c FROM students").fetchone()["c"]
    totals = conn.execute(
        "SELECT COUNT(*) c, SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) p FROM attendance"
    ).fetchone()
    total_records = totals["c"] or 0
    present_total = totals["p"] or 0

    class_rows = conn.execute(
        "SELECT s.class_name, s.section, "
        "COUNT(DISTINCT s.id) AS student_count, "
        "COUNT(a.id) AS total_records, "
        "SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present_count "
        "FROM students s LEFT JOIN attendance a ON a.student_id = s.id "
        "GROUP BY s.class_name, s.section ORDER BY s.class_name, s.section"
    ).fetchall()
    conn.close()
    return _build_attendance_analytics(total_students, total_records, present_total, class_rows)


def get_teacher_attendance_analytics(teacher_user_id):
    conn = db.get_connection()
    total_students = conn.execute(
        "SELECT COUNT(*) c FROM students WHERE teacher_id = ?", (teacher_user_id,)
    ).fetchone()["c"]
    totals = conn.execute(
        "SELECT COUNT(a.id) c, SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) p "
        "FROM attendance a JOIN students s ON s.id = a.student_id WHERE s.teacher_id = ?",
        (teacher_user_id,),
    ).fetchone()
    total_records = totals["c"] or 0
    present_total = totals["p"] or 0

    class_rows = conn.execute(
        "SELECT s.class_name, s.section, "
        "COUNT(DISTINCT s.id) AS student_count, "
        "COUNT(a.id) AS total_records, "
        "SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present_count "
        "FROM students s LEFT JOIN attendance a ON a.student_id = s.id "
        "WHERE s.teacher_id = ? "
        "GROUP BY s.class_name, s.section ORDER BY s.class_name, s.section",
        (teacher_user_id,),
    ).fetchall()
    conn.close()
    return _build_attendance_analytics(total_students, total_records, present_total, class_rows)


def _build_attendance_analytics(total_students, total_records, present_total, class_rows):
    absent_total = total_records - present_total
    average_percentage = round((present_total / total_records) * 100, 1) if total_records else None

    class_summary = []
    for r in class_rows:
        total = r["total_records"] or 0
        present = r["present_count"] or 0
        percentage = round((present / total) * 100, 1) if total else None
        class_summary.append({
            "class_name": r["class_name"],
            "section": r["section"],
            "student_count": r["student_count"],
            "total_records": total,
            "present": present,
            "absent": total - present,
            "percentage": percentage,
        })

    return {
        "total_students": total_students,
        "total_records": total_records,
        "present_total": present_total,
        "absent_total": absent_total,
        "average_percentage": average_percentage,
        "class_summary": class_summary,
    }


def get_admin_marks_analytics():
    conn = db.get_connection()
    totals = conn.execute(
        "SELECT SUM(marks_obtained) o, SUM(max_marks) m, "
        "COUNT(DISTINCT student_id) sc, COUNT(*) rc FROM marks"
    ).fetchone()
    subject_rows = conn.execute(
        "SELECT subject, COUNT(*) rc, SUM(marks_obtained) o, SUM(max_marks) m "
        "FROM marks GROUP BY subject ORDER BY subject"
    ).fetchall()
    conn.close()
    return _build_marks_analytics(totals, subject_rows)


def get_teacher_marks_analytics(teacher_user_id):
    conn = db.get_connection()
    totals = conn.execute(
        "SELECT SUM(m.marks_obtained) o, SUM(m.max_marks) mx, "
        "COUNT(DISTINCT m.student_id) sc, COUNT(*) rc "
        "FROM marks m JOIN students s ON s.id = m.student_id WHERE s.teacher_id = ?",
        (teacher_user_id,),
    ).fetchone()
    subject_rows = conn.execute(
        "SELECT m.subject, COUNT(*) rc, SUM(m.marks_obtained) o, SUM(m.max_marks) mx "
        "FROM marks m JOIN students s ON s.id = m.student_id WHERE s.teacher_id = ? "
        "GROUP BY m.subject ORDER BY m.subject",
        (teacher_user_id,),
    ).fetchall()
    conn.close()
    # Normalize column names (mx instead of m) so _build_marks_analytics can be shared.
    totals = {"o": totals["o"], "m": totals["mx"], "sc": totals["sc"], "rc": totals["rc"]}
    subject_rows = [{"subject": r["subject"], "rc": r["rc"], "o": r["o"], "m": r["mx"]} for r in subject_rows]
    return _build_marks_analytics(totals, subject_rows)


def _build_marks_analytics(totals, subject_rows):
    total_obtained = totals["o"] or 0
    total_max = totals["m"] or 0
    overall_percentage = round((total_obtained / total_max) * 100, 1) if total_max else None

    subject_summary = []
    for r in subject_rows:
        obtained = r["o"] or 0
        maximum = r["m"] or 0
        percentage = round((obtained / maximum) * 100, 1) if maximum else None
        subject_summary.append({
            "subject": r["subject"],
            "record_count": r["rc"],
            "average_percentage": percentage,
        })

    return {
        "students_with_marks": totals["sc"] or 0,
        "record_count": totals["rc"] or 0,
        "overall_percentage": overall_percentage,
        "subject_summary": subject_summary,
    }


@app.route("/analytics/admin")
@role_required("admin")
def analytics_admin():
    user = current_user()
    attendance = get_admin_attendance_analytics()
    marks = get_admin_marks_analytics()
    return render_template("analytics_admin.html", user=user, attendance=attendance, marks=marks)


@app.route("/analytics/teacher")
@role_required("teacher")
def analytics_teacher():
    user = current_user()
    attendance = get_teacher_attendance_analytics(user["id"])
    marks = get_teacher_marks_analytics(user["id"])
    return render_template("analytics_teacher.html", user=user, attendance=attendance, marks=marks)


# ---------------------------------------------------------------------------
# Placeholder pages for upcoming features (kept intentionally simple for now)
# ---------------------------------------------------------------------------

PLACEHOLDER_FEATURES = {
    "profile": "Student Profile",
    "attendance": "Attendance",
    "marks": "Marks",
    "notifications": "Notifications",
    "messages": "Parent-Teacher Messaging",
    "analytics": "Basic Analytics",
    "assistant": "AI College Assistant",
}


@app.route("/feature/<feature_key>")
@login_required
def feature_placeholder(feature_key):
    title = PLACEHOLDER_FEATURES.get(feature_key, "Feature")
    return render_template("placeholder.html", title=title)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
