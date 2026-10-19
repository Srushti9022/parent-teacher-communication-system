"""Messages between teachers and parents, stored in the existing `messages` collection.

Rules checked on the server for every message:
  * sender and receiver are real, active users (senderId / receiverId are users.userId values)
  * one side is a Parent and the other a Teacher
  * the student belongs to that parent, and the teacher is allowed to see that student
    (class teacher of the student's class, or a teacher of a subject taught to that grade)"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

import access
from auth import role_required
from db import col

bp = Blueprint("messages", __name__, url_prefix="/messages")
SORT = [("sentDate", -1), ("messageId", -1)]


def _child_filter():
    """For parents, only messages about their own child are ever listed or opened."""
    if g.user["role"] == "Parent":
        return {"studentId": access.parent_and_child(g.user)[1]["studentId"]}
    return {}


@bp.route("/")
@role_required("Teacher", "Parent")
def inbox():
    box = "sent" if request.args.get("box") == "sent" else "inbox"
    flt = dict(_child_filter(), **{("senderId" if box == "sent" else "receiverId"): g.user["userId"]})
    items, page, pages, total = access.paginate("messages", flt, SORT, request.args.get("page", 1, type=int), 15)
    users, students = access.message_lookup(items)
    return render_template("messages/inbox.html", box=box, items=items, page=page, pages=pages, total=total,
                           users=users, students=students)


@bp.route("/<message_id>")
@role_required("Teacher", "Parent")
def view(message_id):
    uid = g.user["userId"]
    msg = col("messages").find_one(dict(_child_filter(), messageId=message_id))
    if not msg or uid not in (msg["senderId"], msg["receiverId"]):
        abort(404, "Message not found.")
    if msg["receiverId"] == uid and msg["status"] != "Read":
        col("messages").update_one({"messageId": message_id, "receiverId": uid}, {"$set": {"status": "Read"}})
        msg["status"] = "Read"
    users, students = access.message_lookup([msg])
    return render_template("messages/view.html", m=msg, users=users, student=students.get(msg["studentId"]), mine=uid)


@bp.route("/<message_id>/status", methods=["POST"])
@role_required("Teacher", "Parent")
def set_status(message_id):
    """Only the RECEIVER can mark a message read or unread (unread = status 'Sent')."""
    new_status = "Read" if request.form.get("status") == "Read" else "Sent"
    result = col("messages").update_one(dict(_child_filter(), messageId=message_id, receiverId=g.user["userId"]),
                                        {"$set": {"status": new_status}})
    if not result.matched_count:
        abort(404, "Message not found.")
    return redirect(url_for("messages.inbox", box="inbox", page=request.form.get("page", 1, type=int)))


@bp.route("/compose", methods=["GET", "POST"])
@role_required("Teacher", "Parent")
def compose():
    role, uid = g.user["role"], g.user["userId"]
    form = {"to": request.values.get("to", ""), "subject": "", "message": ""}
    reply_to = request.args.get("reply_to")
    if reply_to and request.method == "GET":
        original = col("messages").find_one(dict(_child_filter(), messageId=reply_to))
        if original and uid in (original["senderId"], original["receiverId"]):
            other = original["senderId"] if original["receiverId"] == uid else original["receiverId"]
            form["to"] = original["studentId"] if role == "Teacher" else \
                (col("users").find_one({"userId": other}, access.NO_PASSWORD) or {}).get("referenceId", "")
            form["subject"] = ("Re: " + original["subject"])[:100]

    if role == "Parent":
        _, child = access.parent_and_child(g.user)
        choices = access.teachers_for_student(child)
        scope = None
    else:
        scope = access.teacher_scope(g.user)
        choices = scope.students()

    if request.method == "POST":
        form = {"to": request.form.get("to", ""), "subject": request.form.get("subject", "").strip(),
                "message": request.form.get("message", "").strip()}
        error, student, receiver, receiver_name = None, None, None, ""
        if len(form["subject"]) < 3 or len(form["subject"]) > 100:
            error = "The subject must be 3 to 100 characters long."
        elif len(form["message"]) < 5 or len(form["message"]) > 1000:
            error = "The message must be 5 to 1000 characters long."
        elif role == "Parent":
            student = child
            match = next((c for c in choices if c["teacher"]["teacherId"] == form["to"]), None)
            if not match:
                error = "Choose one of your child's teachers from the list."
            else:
                receiver, receiver_name = match["user"], match["teacher"]["name"]
        else:
            student = col("students").find_one({"studentId": form["to"]})
            if not student or not scope.can_view_student(student):
                error = "Choose a student from your own classes."
            else:
                parent, receiver = access.parent_for_student(student)
                if not receiver:
                    error = "This student's parent has no active login account, so a message cannot be delivered."
                receiver_name = parent["name"] if parent else ""
        if error:
            flash(error, "danger")
        else:
            doc = {"messageId": "MSG%04d" % (access.next_number("messages", "messageId", "MSG") + 1),
                   "senderId": uid, "senderRole": role.lower(), "receiverId": receiver["userId"],
                   "receiverRole": receiver["role"].lower(), "studentId": student["studentId"],
                   "subject": form["subject"], "message": form["message"], "sentDate": access.utc_now(), "status": "Sent"}
            col("messages").insert_one(doc)
            flash("Message sent to %s." % receiver_name, "success")
            return redirect(url_for("messages.inbox", box="sent"))
    return render_template("messages/compose.html", form=form, choices=choices, role=role)
