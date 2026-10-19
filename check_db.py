"""Read-only health check of the smart_school database. It never writes anything.
Run:  python check_db.py"""
import sys

EXPECTED = {"users": 1041, "students": 500, "parents": 500, "teachers": 40, "classes": 15, "subjects": 35,
            "attendance": 14000, "marks": 7000, "messages": 600, "notifications": 150}


def run(database):
    lines, problems = [], 0

    def check(ok, text):
        nonlocal problems
        problems += 0 if ok else 1
        lines.append(("  OK    " if ok else "  WARN  ") + text)

    lines.append("Collections (documents found / documents in the original dump):")
    for name, expected in EXPECTED.items():
        n = database[name].count_documents({})
        check(n >= expected, "%-14s %6d / %d" % (name, n, expected))
    users = {u["userId"]: u for u in database["users"].find({}, {"password": 0})}
    parents = {p["parentId"]: p for p in database["parents"].find()}
    teachers = {t["teacherId"] for t in database["teachers"].find()}
    classes = {c["classid"] for c in database["classes"].find()}
    students = list(database["students"].find())
    lines.append("Relationships:")
    check(all(s["parentId"] in parents and parents[s["parentId"]]["studentId"] == s["studentId"] for s in students),
          "every student <-> parent link matches both ways")
    check(all(s["classId"] in classes for s in students), "every student belongs to an existing class")
    check(all(u["referenceId"] in parents for u in users.values() if u["role"] == "Parent"), "every Parent login points to a parents record")
    check(all(u["referenceId"] in teachers for u in users.values() if u["role"] == "Teacher"), "every Teacher login points to a teachers record")
    check(all(c["classteacherid"] in teachers for c in database["classes"].find()), "every class teacher exists")
    bad = [m["messageId"] for m in database["messages"].find()
           if m["senderId"] not in users or m["receiverId"] not in users]
    check(not bad, "every message sender and receiver is a real user id")
    lines.append("Added by this portal (new collections; fine if empty):")
    lines.append("  app_credentials: %d   audit_log: %d" % (database["app_credentials"].count_documents({}),
                                                        database["audit_log"].count_documents({})))
    lines.append("Result: %s" % ("all checks passed" if not problems else "%d warning(s), see above" % problems))
    return lines, problems


if __name__ == "__main__":
    import config
    from db import get_db
    try:
        output, issues = run(get_db())
    except Exception as exc:
        print("Could not read the database (%s). Is MongoDB running, and is MONGO_URI in .env correct?" % type(exc).__name__)
        sys.exit(2)
    print("Database:", config.DB_NAME)
    print("\n".join(output))
    sys.exit(1 if issues else 0)
