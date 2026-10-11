# Smart School Portal (Flask + MongoDB)

A simple parent-teacher communication portal that runs on your **existing** `smart_school` MongoDB database.
Python, Flask, PyMongo, Jinja2 and Bootstrap. No React, no Node.

## What it does

| Role | Can do |
|---|---|
| **Admin** | Dashboard, view users, students, teachers, classes and notifications (read-only, filter and search). |
| **Teacher** | View their students, attendance and marks; **update marks**; **take or correct attendance**; read and send messages to parents; mark received messages read or unread. |
| **Parent** | View **only their own child's** marks, attendance, notifications and messages; send messages to their child's teachers. |

Changes a teacher saves are written straight to MongoDB, so the parent sees them as soon as they refresh.

## Who is "authorised"? (worked out from your data)

* **Class teacher** (`classes.classteacherid`): can view every subject for the students of that class.
* **Subject teacher** (`subjects.teacherId`): can view *and edit* marks and attendance for **their own subject only**.
  Subjects are stored per grade (`className`), not per section, so a subject teacher covers all sections of that grade.
* **Messages**: a parent may message the class teacher and the subject teachers of their child's grade. A teacher may
  message the parent of any student they are authorised to see. (In the original 600 messages every teacher is the class
  teacher. If you want class teachers only, change `teachers_for_student()` in `access.py`.)
* A parent is tied to exactly one child (`parents.studentId` must match `students.parentId`, checked on every request).
* Notifications a parent sees: audience `All`, `Parents`, and `Class` for their child's class.

## Windows setup

You need: **Python 3.10+** (tick "Add python.exe to PATH" in the installer), **MongoDB Community Server**,
and the **MongoDB Database Tools** (they provide `mongorestore`). MongoDB Compass is optional but handy.

**1. Put this folder inside your GitHub download**, next to the `database` folder, so it looks like:

```
Smart-School-Database-master\
    database\smart_school\...        <- the dump
    smart-school-portal\             <- this folder
```

**2. Load the database into MongoDB (once).** Open *Command Prompt* in `Smart-School-Database-master`:

```
mongorestore --db smart_school database\smart_school
```

Do not add `--drop`. Skip this step if `smart_school` is already in MongoDB.

**3. Install and start the portal.** In *Command Prompt*:

```
cd smart-school-portal
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
notepad .env
```

Paste the long random text printed above after `SECRET_KEY=` in Notepad, save and close. Then:

```
python check_db.py
python app.py
```

`check_db.py` is read-only and confirms the database was restored and its links are consistent.
Open **http://127.0.0.1:5000** in your browser. Stop the portal with `Ctrl+C`.

Next time, only these are needed: `cd smart-school-portal`, `venv\Scripts\activate`, `python app.py`.

## Signing in

| Role | Example email |
|---|---|
| Admin | `admin@smartschool.edu` |
| Teacher (class teacher of 6A, teaches English) | `nehadeshmukh1@smartschool.edu` |
| Parent (of Aarav Kulkarni, class 6A) | `rajeshjadhav1@parent.smartschool.edu` |

Every account in the dump has the same starting password, stored in the `password` field of `users`. To see it, run
`mongosh smart_school --eval "db.users.findOne({userId:'U001'},{password:1,_id:0})"` or open it in Compass.
It is not written in this README on purpose, so it never ends up on GitHub.

On first sign-in you must choose a new password. Other emails: browse the *Users* page as Admin.
Student accounts exist in the database, but this portal is for Admin, Teacher and Parent only.

## How passwords are handled

The existing `users.password` values are **plain text and identical for all 1,041 users**. This app never changes,
copies or shows that field. On a person's first sign-in the old password is checked once and a **hashed** password is
saved in a **new** collection, `app_credentials`. From then on only the hash is used and the old value stops working
in this portal. (It still sits in `users`; replace it outside this app when you can.)
To let someone sign in with the original password again, delete their one document in `app_credentials` (Compass).

## Your existing data is safe

* Nothing is deleted, replaced, reset or bulk-updated, and no indexes are added to your collections.
* Changes happen one document at a time: a mark's `marksObtained`, `percentage` and `grade`; an attendance `status`;
  a message's `status`. New attendance and message documents use the **same fields** as the existing ones.
* Two **new** collections are added: `app_credentials` (hashed passwords) and `audit_log` (who changed which mark or
  attendance record, with the old value).
* The grade scale (A+ 90-100, A 80-89, B+ 70-79, B 60-69, C 50-59, D 40-49, F below 40) was read from your 7,000
  marks; a test confirms it reproduces every existing grade and percentage.

## Limitations (missing or ambiguous data)

* **No timetable and no teacher-per-section data**: that is why subject teachers cover every section of a grade.
* **Messages have no thread or reply id**: replies are separate messages (subject "Re: ...").
* **Marks cannot be created**: your data already has a record for every student, subject and exam, so teachers
  can only *edit* marks. No exams are invented.
* **Attendance** is one record per student, subject and date. Teachers can correct a date or record a new one
  (date not in the future).
* **Teachers do not see notifications** and admins cannot send or edit anything (not requested).
* **Ids are numbered "next number up"** (`MSG0601`, `AT014001`). Two people saving at the exact same second
  could collide; fine for a school project, not for a very busy system.
* No password reset page and no email sending.

## Tests

```
python -m unittest discover -s tests -v
```

The 25 tests load your real dump **into memory** (from `..\database\smart_school`; set `DUMP_DIR` to use another
folder) and never touch your MongoDB. They check logins and passwords, role access, parent isolation, marks and
attendance updates reaching parents, message rules and that existing data stays unchanged.

## Files

`app.py` start here, `auth.py` login, passwords and CSRF, `access.py` who may see what, `admin.py` `teacher.py`
`parent.py` `messages.py` the pages, `templates/` Jinja pages, `static/style.css`, `check_db.py` read-only check.

## Security notes

Server-side role checks on every route, hashed passwords, CSRF token on every form, login lockout after 5 wrong
tries (10 minutes), HttpOnly cookies, `.env` kept out of git. Before real use: run behind HTTPS and a production
server (for example `waitress`), not `python app.py`.
