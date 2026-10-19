"""Academic progress calculations and the rule-based progress report for the parent dashboard.

Everything here is a plain function of the data passed in: no database access, no caching.
The parent route loads the child's CURRENT marks and attendance from MongoDB on every page
load and passes them in, so what the parent sees is always built from the latest saved data.

Rules used (also listed in the README):
  * A marks record counts only if marksObtained and maximumMarks are numbers, maximumMarks > 0
    and 0 <= marksObtained <= maximumMarks. Other records are skipped, never turned into zeros.
  * One result per (subject, exam). If a student somehow had two records for the same subject
    and exam, the one with the highest markId is used and the other is not counted.
  * Overall % for an exam = total marks obtained / total maximum marks x 100, over the subjects
    that have a valid result for that exam (NOT an average of subject percentages).
  * EXAM ORDER: the marks documents hold no exam date, only examName. The order therefore comes
    from grading.EXAM_ORDER (Unit Test 1, then Mid-Term); unknown names follow alphabetically.
    The page says that this order is not date-verified.
  * Change is measured in percentage points between the latest and the previous exam:
    +2.0 or more = Improving, -2.0 or less = Declining, in between = Stable.
"""
import grading

CHANGE_THRESHOLD = 2.0       # percentage points
MIN_SUBJECTS_TO_RANK = 3     # strongest / needs-attention need at least this many subjects
STRONG_FROM = 75.0           # subject is "strong" at or above this % (all exams combined)
ATTENTION_BELOW = 60.0       # subject "needs attention" below this % (all exams combined)
LOW_ATTENDANCE = 75.0        # same level the attendance badges use
BIG_SUBJECT_CHANGE = 5.0     # subject change worth mentioning in the report (points)

CHRONOLOGY_NOTE = ("Exam order follows the school's usual sequence (Unit Test 1, then Mid-Term). "
                   "The database holds no exam dates, so this order is assumed, not date-verified.")

CAREER_PATHS = [   # (word in subject name, possibilities to explore)
    ("math", "engineering, data science, finance and accounting, architecture"),
    ("science", "medicine and healthcare, research, engineering, agriculture and the environment"),
    ("computer", "software and app development, cybersecurity, IT support, game design"),
    ("english", "journalism, law, teaching, publishing and communication"),
    ("social", "civil services, law, economics, public policy and history"),
    ("hindi", "writing and translation, media, teaching, public service"),
    ("marathi", "writing and translation, media, teaching, public service"),
]


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def valid_result(mark):
    obtained, maximum = mark.get("marksObtained"), mark.get("maximumMarks")
    return _is_number(obtained) and _is_number(maximum) and maximum > 0 and 0 <= obtained <= maximum


def unique_results(marks):
    """({(subjectId, examName): mark}, duplicates_ignored, invalid_skipped)"""
    chosen, duplicates, invalid = {}, 0, 0
    for m in marks:
        if not valid_result(m) or "subjectId" not in m or "examName" not in m:
            invalid += 1
            continue
        key = (m["subjectId"], m["examName"])
        if key in chosen:
            duplicates += 1
            if str(m.get("markId", "")) > str(chosen[key].get("markId", "")):
                chosen[key] = m
        else:
            chosen[key] = m
    return chosen, duplicates, invalid


def pct(obtained, maximum):
    return round(obtained * 100 / maximum, 1)


def fmt_points(change):
    """+7.0 / -13.4 with a proper minus sign."""
    return ("+%.1f" % change) if change >= 0 else ("\u2212%.1f" % abs(change))


def status_for(change):
    if change is None:
        return None
    if change >= CHANGE_THRESHOLD:
        return "Improving"
    if change <= -CHANGE_THRESHOLD:
        return "Declining"
    return "Stable"


# ------------------------------------------------------------------ marks progress
def build_progress(marks, subjects):
    """`subjects` is {subjectId: subject document} for the child's grade."""
    chosen, duplicates, invalid = unique_results(marks)
    exams = sorted({exam for (_, exam) in chosen}, key=grading.exam_sort_key)
    ids = sorted(set(subjects) | {sid for (sid, _) in chosen})
    name = lambda sid: subjects.get(sid, {}).get("subjectName", sid)

    overall = []
    for exam in exams:
        items = [m for (_, e), m in chosen.items() if e == exam]
        obtained = sum(m["marksObtained"] for m in items)
        maximum = sum(m["maximumMarks"] for m in items)
        overall.append({"obtained": obtained, "maximum": maximum, "percentage": pct(obtained, maximum),
                        "subjects": len(items)})

    series = []
    for sid in ids:
        points = []
        for exam in exams:
            m = chosen.get((sid, exam))
            points.append(None if not m else {"obtained": m["marksObtained"], "maximum": m["maximumMarks"],
                                              "percentage": pct(m["marksObtained"], m["maximumMarks"])})
        if any(points):
            series.append({"id": sid, "name": name(sid), "points": points})
    series_by_id = {s["id"]: s for s in series}

    aggregate = []   # each subject over ALL its exams: total obtained / total maximum
    for sid in ids:
        items = [m for (s, _), m in chosen.items() if s == sid]
        if items:
            maximum = sum(m["maximumMarks"] for m in items)
            aggregate.append({"id": sid, "name": name(sid), "percentage": pct(sum(m["marksObtained"] for m in items), maximum),
                              "exams": len(items)})

    latest_rows = []
    if exams:
        last, before = len(exams) - 1, len(exams) - 2
        for sid in ids:
            pts = series_by_id.get(sid, {}).get("points", [None] * len(exams))
            now, earlier = pts[last], (pts[before] if before >= 0 else None)
            row = {"id": sid, "name": name(sid), "recorded": now is not None, "obtained": None, "maximum": None,
                   "percentage": None, "grade": None, "previous": earlier["percentage"] if earlier else None, "change": None}
            if now:
                row.update(obtained=now["obtained"], maximum=now["maximum"], percentage=now["percentage"],
                           grade=grading.grade_for(now["percentage"]))
                if earlier:
                    row["change"] = round(now["percentage"] - earlier["percentage"], 1)
            latest_rows.append(row)

    summary = summarise(exams, overall, aggregate, chosen)
    return {"hasData": bool(exams), "exams": exams, "overall": overall, "series": series, "aggregate": aggregate,
            "latest_rows": latest_rows, "summary": summary, "subjectsExpected": len(subjects),
            "chronologyNote": CHRONOLOGY_NOTE, "quality": {"duplicatesIgnored": duplicates, "invalidSkipped": invalid},
            "chart": {"exams": exams, "overall": overall, "subjects": series, "subjectsExpected": len(subjects)}}


def summarise(exams, overall, aggregate, chosen):
    out = {"latest": None, "previous": None, "change": None, "changeText": None, "status": None,
           "likeForLike": True, "strongest": None, "attention": None, "rankNote": None}
    if not exams:
        return out
    out["latest"] = {"exam": exams[-1], **overall[-1]}
    if len(exams) >= 2:
        out["previous"] = {"exam": exams[-2], **overall[-2]}
        out["change"] = round(overall[-1]["percentage"] - overall[-2]["percentage"], 1)
        out["changeText"] = fmt_points(out["change"])
        out["status"] = status_for(out["change"])
        now = {s for (s, e) in chosen if e == exams[-1]}
        before = {s for (s, e) in chosen if e == exams[-2]}
        out["likeForLike"] = now == before
    if len(aggregate) >= MIN_SUBJECTS_TO_RANK:
        top = max(a["percentage"] for a in aggregate)
        low = min(a["percentage"] for a in aggregate)
        if top != low:
            out["strongest"] = {"names": [a["name"] for a in aggregate if a["percentage"] == top], "percentage": top}
            out["attention"] = {"names": [a["name"] for a in aggregate if a["percentage"] == low], "percentage": low}
        else:
            out["rankNote"] = "All subjects have the same percentage, so none stands out."
    else:
        out["rankNote"] = "Needs results in at least %d subjects." % MIN_SUBJECTS_TO_RANK
    return out


# ------------------------------------------------------------------ attendance
def valid_attendance(record):
    """A usable attendance record has a status of Present or Absent AND a real date."""
    d = record.get("date")
    return record.get("status") in ("Present", "Absent") and hasattr(d, "year") and hasattr(d, "month")


def analyse_attendance(records):
    counted = [a for a in records if valid_attendance(a)]
    out = {"total": len(counted), "present": 0, "absent": 0, "percentage": None, "first": None, "last": None,
           "days": 0, "by_subject": [], "low_subjects": [], "worst_day": None, "latest_day": None}
    if not counted:
        return out
    out["present"] = sum(1 for a in counted if a["status"] == "Present")
    out["absent"] = len(counted) - out["present"]
    out["percentage"] = pct(out["present"], len(counted))
    per_day, per_subject = {}, {}
    for a in counted:
        day = per_day.setdefault(a["date"], [0, 0])
        day[1] += 1
        day[0] += a["status"] == "Present"
        sub = per_subject.setdefault(a["subjectId"], [0, 0])
        sub[1] += 1
        sub[0] += a["status"] == "Present"
    days = sorted(per_day)
    out.update(first=days[0], last=days[-1], days=len(days))
    out["by_subject"] = [{"id": sid, "present": p, "total": t, "percentage": pct(p, t)} for sid, (p, t) in sorted(per_subject.items())]
    out["low_subjects"] = [s["id"] for s in out["by_subject"] if s["total"] >= 3 and s["percentage"] < LOW_ATTENDANCE]
    absences = {d: per_day[d][1] - per_day[d][0] for d in days}
    worst = max(days, key=lambda d: absences[d])
    if len(days) >= 2 and absences[worst] >= 2:
        out["worst_day"] = {"date": worst, "absent": absences[worst], "total": per_day[worst][1]}
    out["latest_day"] = {"date": days[-1], "present": per_day[days[-1]][0], "total": per_day[days[-1]][1]}
    return out


def _day(d):
    return d.strftime("%d %b %Y")


# ------------------------------------------------------------------ rule-based report
def build_report(progress, attendance, subject_names):
    """Plain-language report built only from the numbers passed in.
    `subject_names` maps subjectId -> name (used for attendance by subject)."""
    s = progress["summary"]
    report = {"source": "rule-based", "summary": None, "exam_changes": [], "strong": [], "attention": [],
              "attendance_summary": None, "attendance_notes": [], "suggestions": [], "careers": None, "limits": []}

    # ---- overall summary
    if not progress["hasData"]:
        report["summary"] = "No exam results have been recorded yet, so there is nothing to summarise."
    else:
        latest = s["latest"]
        text = "The latest result is %s%% overall in %s (grade %s), from %d subject%s." % (
            latest["percentage"], latest["exam"], grading.grade_for(latest["percentage"]), latest["subjects"],
            "" if latest["subjects"] == 1 else "s")
        if s["previous"]:
            prev = s["previous"]
            if s["change"] == 0:
                text += " That is the same as the %s%% in %s." % (prev["percentage"], prev["exam"])
            else:
                text += " That is %.1f percentage points %s the %s%% in %s." % (
                    abs(s["change"]), "above" if s["change"] > 0 else "below", prev["percentage"], prev["exam"])
            text += " Overall trend: %s." % s["status"].lower()
        else:
            text += " Only one exam is recorded so far, so no trend can be shown yet."
        report["summary"] = text

    # ---- exam-to-exam changes
    exams, overall = progress["exams"], progress["overall"]
    for i in range(1, len(exams)):
        change = round(overall[i]["percentage"] - overall[i - 1]["percentage"], 1)
        word = "unchanged" if change == 0 else ("up %.1f" % change if change > 0 else "down %.1f" % abs(change))
        report["exam_changes"].append("%s to %s: overall %s%% to %s%% (%s%s)." % (
            exams[i - 1], exams[i], overall[i - 1]["percentage"], overall[i]["percentage"], word,
            "" if change == 0 else " percentage points"))
    movers = [r for r in progress["latest_rows"] if r["change"] is not None and abs(r["change"]) >= BIG_SUBJECT_CHANGE]
    if movers and s["previous"]:
        drops = sorted([r for r in movers if r["change"] < 0], key=lambda r: r["change"])[:3]
        gains = sorted([r for r in movers if r["change"] > 0], key=lambda r: -r["change"])[:3]
        if gains:
            report["exam_changes"].append("Biggest gains since %s: %s." % (s["previous"]["exam"], ", ".join(
                "%s (%s)" % (r["name"], fmt_points(r["change"])) for r in gains)))
        if drops:
            report["exam_changes"].append("Biggest drops since %s: %s." % (s["previous"]["exam"], ", ".join(
                "%s (%s)" % (r["name"], fmt_points(r["change"])) for r in drops)))
    if s["previous"] and not s["likeForLike"]:
        report["limits"].append("The latest and previous exams do not cover the same subjects, so the overall comparison is not like for like.")
    maxima = {p["maximum"] for p in overall}
    if len(maxima) > 1:
        report["limits"].append("Exams have different total marks, so percentages (not raw marks) are compared.")
    if progress["hasData"]:
        report["limits"].append(progress["chronologyNote"])

    # ---- strong subjects / needs attention (all exams combined, needs enough subjects)
    if len(progress["aggregate"]) >= MIN_SUBJECTS_TO_RANK:
        latest_f = {r["name"] for r in progress["latest_rows"] if r["grade"] == "F"}
        for a in sorted(progress["aggregate"], key=lambda a: -a["percentage"]):
            if a["percentage"] >= STRONG_FROM:
                report["strong"].append({"name": a["name"], "percentage": a["percentage"]})
        for a in sorted(progress["aggregate"], key=lambda a: a["percentage"]):
            if a["percentage"] < ATTENTION_BELOW or a["name"] in latest_f:
                report["attention"].append({"name": a["name"], "percentage": a["percentage"],
                                            "gradeF": a["name"] in latest_f})
    elif progress["hasData"]:
        report["limits"].append("Strong and weaker subjects need results in at least %d subjects." % MIN_SUBJECTS_TO_RANK)

    # ---- attendance
    att = attendance
    if not att["total"]:
        report["attendance_summary"] = "No attendance has been recorded yet."
    else:
        report["attendance_summary"] = "Attendance is %s%% (%d present, %d absent) across %d records from %s to %s." % (
            att["percentage"], att["present"], att["absent"], att["total"], _day(att["first"]), _day(att["last"]))
        if att["low_subjects"]:
            report["attendance_notes"].append("Attendance is below %d%% in: %s." % (
                LOW_ATTENDANCE, ", ".join(subject_names.get(i, i) for i in att["low_subjects"])))
        if att["worst_day"]:
            w = att["worst_day"]
            report["attendance_notes"].append("The most absences were on %s (%d of %d classes that day)." % (_day(w["date"]), w["absent"], w["total"]))
        l = att["latest_day"]
        report["attendance_notes"].append("On the latest recorded day, %s, %d of %d classes were attended." % (_day(l["date"]), l["present"], l["total"]))
        report["attendance_notes"].append("Records cover %d day%s so far, so these are early signs rather than long-term trends." % (
            att["days"], "" if att["days"] == 1 else "s"))

    # ---- suggestions (each one tied to a real number above)
    tips = report["suggestions"]
    if s["status"] == "Declining":
        tips.append("Overall results fell by %.1f percentage points between %s and %s. Ask the class teacher which topics or skills "
                    "were affected, and go through the marked papers together." % (abs(s["change"]), s["previous"]["exam"], s["latest"]["exam"]))
    elif s["status"] == "Improving":
        tips.append("Results are rising. Acknowledge the effort and keep the routines that are working.")
    elif s["status"] == "Stable":
        tips.append("Results are steady between exams. A small, specific goal for the next exam, such as lifting one subject by 5 points, can help.")
    for a in report["attention"][:3]:
        tips.append("%s (%s%% overall%s): try a short, regular practice routine, for example 20 to 30 minutes a few times a week, "
                    "and ask the subject teacher which topics lost marks." % (a["name"], a["percentage"], ", graded F in the latest exam" if a["gradeF"] else ""))
    if att["total"] and att["percentage"] < LOW_ATTENDANCE:
        tips.append("Attendance is %s%%, below %d%%. Regular attendance supports learning; if there are barriers, talk to the class teacher." % (att["percentage"], LOW_ATTENDANCE))
    if report["strong"]:
        tips.append("Keep stretching strengths such as %s with extra reading or enrichment activities." % ", ".join(x["name"] for x in report["strong"][:3]))
    if tips:
        tips.append("Use the Messages page to ask a teacher about specific topics.")
    else:
        tips.append("Suggestions will appear once there are enough marks or attendance records.")

    report["careers"] = _careers(progress)
    return report


def _careers(progress):
    """Only offered when the SAME subject is strong in every exam it appears in, across at least 2 exams."""
    intro = ("These are possibilities to explore, not conclusions. Marks show only part of a child's strengths; interests, "
             "activities and teacher feedback matter just as much, and there is plenty of time to decide.")
    if len(progress["exams"]) < 2 or len(progress["aggregate"]) < MIN_SUBJECTS_TO_RANK:
        return {"enough": False, "intro": intro,
                "message": "Not enough evidence yet. This needs results in at least %d subjects across at least 2 exams." % MIN_SUBJECTS_TO_RANK, "entries": []}
    items = []
    for sub in progress["series"]:
        pts = [p for p in sub["points"] if p]
        if len(pts) >= 2 and all(p["percentage"] >= STRONG_FROM for p in pts):
            paths = next((text for word, text in CAREER_PATHS if word in sub["name"].lower()), None)
            if paths:
                items.append({"subject": sub["name"], "paths": paths})
    if not items:
        return {"enough": False, "intro": intro, "entries": [],
                "message": "Not enough evidence yet. No subject has been consistently strong (%d%% or more) in every recorded exam." % STRONG_FROM}
    return {"enough": True, "intro": intro, "entries": items, "message": None}


# ------------------------------------------------------------------ data for the three dashboard charts
# (doughnut: attendance, bars: marks by subject, line: attendance by month)
# Every number the charts show is calculated here; the JavaScript only draws what it is given.
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MIN_MONTHS_FOR_TREND = 2


def attendance_pie(attendance):
    """Present / Absent counts and percentages from analyse_attendance(); None if no valid records."""
    if not attendance["total"]:
        return None
    present, absent, total = attendance["present"], attendance["absent"], attendance["total"]
    return {"present": present, "absent": absent, "total": total, "percentage": attendance["percentage"],
            "presentPct": pct(present, total), "absentPct": pct(absent, total)}


def marks_bars(prog):
    """Per exam: one bar per subject with a valid result (same validity and de-duplication rules as the
    progress chart, because it is built from the same build_progress() result). None if there are no results."""
    if not prog["hasData"]:
        return None
    by_exam = {}
    for i, exam in enumerate(prog["exams"]):
        rows = [{"subject": s["name"], "obtained": s["points"][i]["obtained"], "maximum": s["points"][i]["maximum"],
                 "percentage": s["points"][i]["percentage"]} for s in prog["series"] if s["points"][i]]
        total = prog["overall"][i]
        by_exam[exam] = {"rows": rows, "axisMax": max(r["maximum"] for r in rows),
                         "caption": "%s: %s of %s marks overall (%s%%) across %d subject%s." % (
                             exam, total["obtained"], total["maximum"], total["percentage"], len(rows), "" if len(rows) == 1 else "s")}
    return {"exams": prog["exams"], "default": prog["exams"][-1], "byExam": by_exam}


def monthly_attendance(records):
    """Attendance % per calendar month, from real `date` and `status` values. Records with a status other than
    Present/Absent or without a real date are left out (and counted). Months with no records do not appear.
    A trend line needs at least MIN_MONTHS_FOR_TREND months; otherwise `chart` is None and `message` explains why."""
    months, skipped = {}, 0
    for a in records:
        if not valid_attendance(a):
            skipped += 1
            continue
        d = a["date"]
        counts = months.setdefault((d.year, d.month), [0, 0])
        counts[1] += 1
        counts[0] += a["status"] == "Present"
    rows = [{"label": "%s %d" % (MONTH_NAMES[m - 1], y), "present": p, "total": t, "percentage": pct(p, t)}
            for (y, m), (p, t) in sorted(months.items())]
    out = {"months": rows, "skipped": skipped, "message": None, "note": None, "highest": None, "lowest": None, "chart": None}
    if not rows:
        out["message"] = "There are no valid attendance records yet, so there is no monthly trend to show."
    elif len(rows) < MIN_MONTHS_FOR_TREND:
        r = rows[0]
        out["message"] = ("A monthly trend needs attendance recorded in at least %d different months. All %d valid records so far are in %s "
                          "(%d present, %s%%), so no trend chart is drawn." % (MIN_MONTHS_FOR_TREND, r["total"], r["label"], r["present"], r["percentage"]))
    else:
        top, low = max(r["percentage"] for r in rows), min(r["percentage"] for r in rows)
        highest = [r["label"] for r in rows if r["percentage"] == top]
        lowest = [r["label"] for r in rows if r["percentage"] == low]
        if top == low:
            out["note"] = "Attendance was %s%% in every month, so there is no highest or lowest month." % top
            highest = lowest = []
        else:
            out["highest"], out["lowest"] = {"labels": highest, "percentage": top}, {"labels": lowest, "percentage": low}
        out["chart"] = {"labels": [r["label"] for r in rows], "percentages": [r["percentage"] for r in rows],
                        "present": [r["present"] for r in rows], "totals": [r["total"] for r in rows],
                        "highest": highest, "lowest": lowest}
    return out


def build_insights(prog, attendance, attendance_records):
    pie, bars, monthly = attendance_pie(attendance), marks_bars(prog), monthly_attendance(attendance_records)
    return {"pie": pie, "bars": bars, "monthly": monthly,
            "chart": {"pie": pie, "bars": bars, "monthly": monthly["chart"]}}   # the JSON given to the browser
