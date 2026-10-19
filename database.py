"""
Database layer for Student-Parent Communication Platform.
Uses plain SQLite (no ORM) to keep things simple and dependency-light.
"""

import sqlite3
import os
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "app.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','teacher','student','parent')),
    full_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    roll_no TEXT NOT NULL,
    class_name TEXT NOT NULL,
    section TEXT,
    academic_year TEXT,
    date_of_birth TEXT,
    parent_id INTEGER REFERENCES users(id),
    teacher_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Present','Absent','Late')),
    marked_by INTEGER REFERENCES users(id),
    UNIQUE(student_id, date)
);

CREATE TABLE IF NOT EXISTS marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    exam_name TEXT NOT NULL,
    marks_obtained REAL NOT NULL,
    max_marks REAL NOT NULL,
    entered_by INTEGER REFERENCES users(id),
    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    target_role TEXT NOT NULL DEFAULT 'all',
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    receiver_id INTEGER NOT NULL REFERENCES users(id),
    content TEXT NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_read INTEGER DEFAULT 0
);
"""


def init_db():
    """Create tables if they don't exist yet, and migrate older databases in place."""
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()

    # Lightweight migration: older copies of this database were created before
    # the academic_year column existed on the students table. Add it in place
    # instead of touching any other part of the schema.
    existing_columns = [row["name"] for row in conn.execute("PRAGMA table_info(students)")]
    if "academic_year" not in existing_columns:
        conn.execute("ALTER TABLE students ADD COLUMN academic_year TEXT")
        conn.commit()

    # Backfill any pre-existing student rows (from before this column existed)
    # that don't have an academic year set yet.
    conn.execute(
        "UPDATE students SET academic_year = '2026-2027' WHERE academic_year IS NULL OR academic_year = ''"
    )
    conn.commit()

    # Lightweight migration for the Marks module: the marks table already
    # existed, so it is reused as-is. We only add an exam_date column (the
    # existing entered_at column is an audit timestamp, not the exam date)
    # and a unique index on (student_id, subject, exam_name) so the app can
    # "update if it already exists" instead of creating duplicate rows.
    marks_columns = [row["name"] for row in conn.execute("PRAGMA table_info(marks)")]
    if "exam_date" not in marks_columns:
        conn.execute("ALTER TABLE marks ADD COLUMN exam_date TEXT")
        conn.commit()

    # Backfill any pre-existing marks rows that don't have an exam_date yet,
    # using the date portion of their entered_at timestamp as a reasonable default.
    conn.execute(
        "UPDATE marks SET exam_date = SUBSTR(entered_at, 1, 10) "
        "WHERE exam_date IS NULL OR exam_date = ''"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_marks_student_subject_exam "
        "ON marks(student_id, subject, exam_name)"
    )
    conn.commit()

    conn.close()


def seed_data(force=False):
    """
    Populate the database with sample users and demo records for all four roles.
    Safe to call every startup - it checks if data already exists first.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS c FROM users")
    existing = cur.fetchone()["c"]
    if existing > 0 and not force:
        conn.close()
        return

    if force:
        cur.executescript("""
            DELETE FROM messages;
            DELETE FROM notifications;
            DELETE FROM marks;
            DELETE FROM attendance;
            DELETE FROM students;
            DELETE FROM users;
        """)

    def add_user(username, password, role, full_name, email, phone=""):
        cur.execute(
            "INSERT INTO users (username, password_hash, role, full_name, email, phone) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, generate_password_hash(password), role, full_name, email, phone),
        )
        return cur.lastrowid

    # --- Admin ---
    admin_id = add_user("admin", "admin123", "admin", "System Administrator", "admin@school.edu")

    # --- Teachers ---
    teacher1_id = add_user("teacher1", "teacher123", "teacher", "Mrs. Anita Sharma", "anita.sharma@school.edu")
    teacher2_id = add_user("teacher2", "teacher123", "teacher", "Mr. Rakesh Verma", "rakesh.verma@school.edu")

    # --- Parents ---
    parent1_id = add_user("parent1", "parent123", "parent", "Mr. Suresh Patil", "suresh.patil@gmail.com")
    parent2_id = add_user("parent2", "parent123", "parent", "Mrs. Kavita Joshi", "kavita.joshi@gmail.com")
    parent3_id = add_user("parent3", "parent123", "parent", "Mr. Vijay Kulkarni", "vijay.kulkarni@gmail.com")

    # --- Students (linked to a parent + a class teacher) ---
    student1_id = add_user("student1", "student123", "student", "Rohan Patil", "rohan.patil@school.edu")
    student2_id = add_user("student2", "student123", "student", "Sneha Joshi", "sneha.joshi@school.edu")
    student3_id = add_user("student3", "student123", "student", "Aditya Kulkarni", "aditya.kulkarni@school.edu")

    cur.execute(
        "INSERT INTO students (user_id, roll_no, class_name, section, academic_year, date_of_birth, parent_id, teacher_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (student1_id, "10A-01", "10th Grade", "A", "2026-2027", "2009-04-12", parent1_id, teacher1_id),
    )
    student1_sid = cur.lastrowid

    cur.execute(
        "INSERT INTO students (user_id, roll_no, class_name, section, academic_year, date_of_birth, parent_id, teacher_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (student2_id, "10A-02", "10th Grade", "A", "2026-2027", "2009-07-23", parent2_id, teacher1_id),
    )
    student2_sid = cur.lastrowid

    cur.execute(
        "INSERT INTO students (user_id, roll_no, class_name, section, academic_year, date_of_birth, parent_id, teacher_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (student3_id, "9B-05", "9th Grade", "B", "2026-2027", "2010-01-30", parent3_id, teacher2_id),
    )
    student3_sid = cur.lastrowid

    # --- Sample attendance (last 5 days) ---
    import datetime
    today = datetime.date.today()
    sample_status = {
        student1_sid: ["Present", "Present", "Absent", "Present", "Late"],
        student2_sid: ["Present", "Late", "Present", "Present", "Present"],
        student3_sid: ["Absent", "Present", "Present", "Present", "Absent"],
    }
    for sid, statuses in sample_status.items():
        for i, status in enumerate(statuses):
            day = today - datetime.timedelta(days=(4 - i))
            cur.execute(
                "INSERT OR IGNORE INTO attendance (student_id, date, status, marked_by) VALUES (?, ?, ?, ?)",
                (sid, day.isoformat(), status, teacher1_id),
            )

    # --- Sample marks ---
    sample_marks = [
        (student1_sid, "Mathematics", "Mid-Term", 78, 100),
        (student1_sid, "Science", "Mid-Term", 85, 100),
        (student1_sid, "English", "Mid-Term", 72, 100),
        (student2_sid, "Mathematics", "Mid-Term", 91, 100),
        (student2_sid, "Science", "Mid-Term", 88, 100),
        (student2_sid, "English", "Mid-Term", 94, 100),
        (student3_sid, "Mathematics", "Mid-Term", 65, 100),
        (student3_sid, "Science", "Mid-Term", 70, 100),
        (student3_sid, "English", "Mid-Term", 60, 100),
    ]
    for sid, subject, exam, obtained, max_m in sample_marks:
        cur.execute(
            "INSERT INTO marks (student_id, subject, exam_name, marks_obtained, max_marks, entered_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, subject, exam, obtained, max_m, teacher1_id),
        )

    # --- Sample notifications ---
    notifications = [
        ("Mid-Term Exams Schedule Released", "Mid-term exams will begin from next Monday. Please check the notice board for the detailed timetable.", "all", admin_id),
        ("Parent-Teacher Meeting", "A parent-teacher meeting is scheduled for this Saturday at 10 AM in the main hall.", "all", admin_id),
        ("Holiday Notice", "School will remain closed on account of a local holiday.", "all", admin_id),
    ]
    for title, message, target, creator in notifications:
        cur.execute(
            "INSERT INTO notifications (title, message, target_role, created_by) VALUES (?, ?, ?, ?)",
            (title, message, target, creator),
        )

    # --- Sample messages between teacher and parent ---
    cur.execute(
        "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
        (teacher1_id, parent1_id, "Hello, Rohan has been doing well in class recently. Keep encouraging his reading habit at home."),
    )
    cur.execute(
        "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
        (parent1_id, teacher1_id, "Thank you for the update. I will make sure he continues reading daily."),
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    seed_data()
    print("Database initialized and seeded at:", DB_PATH)
