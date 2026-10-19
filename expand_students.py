"""
One-time data migration: expand the student dataset from 3 to 10 students
for a more realistic demo. Safe to run multiple times (checks for existing
usernames before inserting anything). Does NOT touch app.py, database.py,
or any existing row - only adds new users/students/parents/attendance/marks.

Run with: python3 expand_students.py
"""

import datetime
from werkzeug.security import generate_password_hash
import database as db

# (student_username, student_full_name, roll_no, class_name, section,
#  teacher_id, parent_username, parent_full_name, dob,
#  attendance_pattern, marks)
NEW_STUDENTS = [
    (
        "student4", "Priya Deshmukh", "10A-03", "10th Grade", "A", 2,
        "parent4", "Mr. Anil Deshmukh", "2009-05-14",
        ["Present", "Present", "Present", "Absent", "Present"],
        {"Mathematics": 82, "Science": 88, "English": 79},
    ),
    (
        "student5", "Arjun Nair", "10A-04", "10th Grade", "A", 2,
        "parent5", "Mrs. Lakshmi Nair", "2009-09-02",
        ["Present", "Late", "Present", "Present", "Absent"],
        {"Mathematics": 74, "Science": 69, "English": 71},
    ),
    (
        "student6", "Isha Reddy", "10A-05", "10th Grade", "A", 2,
        "parent6", "Mr. Srinivas Reddy", "2009-11-20",
        ["Absent", "Present", "Present", "Present", "Present"],
        {"Mathematics": 91, "Science": 85, "English": 93},
    ),
    (
        "student7", "Karan Mehta", "10A-06", "10th Grade", "A", 2,
        "parent7", "Mrs. Pooja Mehta", "2009-03-08",
        ["Present", "Present", "Absent", "Present", "Late"],
        {"Mathematics": 65, "Science": 72, "English": 68},
    ),
    (
        "student8", "Ananya Iyer", "9B-06", "9th Grade", "B", 3,
        "parent8", "Mr. Ramesh Iyer", "2010-06-17",
        ["Present", "Present", "Present", "Present", "Absent"],
        {"Mathematics": 88, "Science": 91, "English": 84},
    ),
    (
        "student9", "Vikram Singh", "9B-07", "9th Grade", "B", 3,
        "parent9", "Mrs. Neha Singh", "2010-02-25",
        ["Late", "Present", "Present", "Absent", "Present"],
        {"Mathematics": 59, "Science": 63, "English": 61},
    ),
    (
        "student10", "Riya Sharma", "9B-08", "9th Grade", "B", 3,
        "parent10", "Mr. Deepak Sharma", "2010-10-11",
        ["Present", "Absent", "Present", "Present", "Present"],
        {"Mathematics": 95, "Science": 89, "English": 92},
    ),
]

ACADEMIC_YEAR = "2026-2027"
EXAM_NAME = "Mid-Term"
EXAM_DATE = "2026-09-15"


def slug_email(full_name, domain):
    parts = full_name.replace("Mr.", "").replace("Mrs.", "").strip().split()
    return f"{parts[0].lower()}.{parts[-1].lower()}@{domain}"


def main():
    conn = db.get_connection()
    cur = conn.cursor()

    existing_usernames = {row["username"] for row in cur.execute("SELECT username FROM users")}

    added_students, skipped = [], []
    today = datetime.date.today()

    for (
        student_username, student_name, roll_no, class_name, section, teacher_id,
        parent_username, parent_name, dob, attendance_pattern, marks,
    ) in NEW_STUDENTS:
        if student_username in existing_usernames or parent_username in existing_usernames:
            skipped.append(student_username)
            continue

        # Parent user
        parent_email = slug_email(parent_name, "gmail.com")
        cur.execute(
            "INSERT INTO users (username, password_hash, role, full_name, email) VALUES (?, ?, 'parent', ?, ?)",
            (parent_username, generate_password_hash("parent123"), parent_name, parent_email),
        )
        parent_id = cur.lastrowid

        # Student user
        student_email = slug_email(student_name, "school.edu")
        cur.execute(
            "INSERT INTO users (username, password_hash, role, full_name, email) VALUES (?, ?, 'student', ?, ?)",
            (student_username, generate_password_hash("student123"), student_name, student_email),
        )
        student_user_id = cur.lastrowid

        # Student academic record, linked to the correct existing teacher
        cur.execute(
            "INSERT INTO students (user_id, roll_no, class_name, section, academic_year, date_of_birth, parent_id, teacher_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (student_user_id, roll_no, class_name, section, ACADEMIC_YEAR, dob, parent_id, teacher_id),
        )
        student_pk = cur.lastrowid

        # Attendance: last 5 days, same pattern as the original seed data
        for i, status in enumerate(attendance_pattern):
            day = today - datetime.timedelta(days=(4 - i))
            cur.execute(
                "INSERT OR IGNORE INTO attendance (student_id, date, status, marked_by) VALUES (?, ?, ?, ?)",
                (student_pk, day.isoformat(), status, teacher_id),
            )

        # Marks: Mid-Term for 3 subjects, out of 100
        for subject, obtained in marks.items():
            cur.execute(
                "INSERT INTO marks (student_id, subject, exam_name, marks_obtained, max_marks, exam_date, entered_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(student_id, subject, exam_name) DO NOTHING",
                (student_pk, subject, EXAM_NAME, obtained, 100, EXAM_DATE, teacher_id),
            )

        added_students.append(student_username)

    conn.commit()
    conn.close()

    print("Added students:", added_students)
    print("Skipped (already existed):", skipped)


if __name__ == "__main__":
    main()
