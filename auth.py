"""Login, logout, password change, CSRF protection and the role_required decorator.

PASSWORDS: the existing users.password field holds one shared plain-text value.
We never change or copy it. On a person's first sign-in it is checked once, and a
SEPARATE hashed password is saved in the NEW collection app_credentials. From then on
only the hash is used, and the person must choose a new password straight away."""
import hmac
import re
import secrets
import time
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import access
import config
from db import col

auth_bp = Blueprint("auth", __name__)

ALLOWED_ROLES = ("Admin", "Teacher", "Parent")
HOME = {"Admin": "admin.dashboard", "Teacher": "teacher.dashboard", "Parent": "parent.dashboard"}
HASH_METHOD = "pbkdf2:sha256"
_failures = {}   # (ip, email) -> list of timestamps of wrong passwords


# ---------------------------------------------------------------- CSRF
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def check_csrf():
    sent = request.form.get("_csrf", "")
    if not sent or not hmac.compare_digest(sent, session.get("_csrf", "")):
        abort(400, "Your form has expired. Go back, refresh the page and try again.")


# ---------------------------------------------------------------- helpers
def load_user():
    """Runs before every request: look up the signed-in user (without the password field)."""
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        user = col("users").find_one({"userId": user_id}, access.NO_PASSWORD)
        if user and user.get("status") == "Active" and user.get("role") in ALLOWED_ROLES:
            g.user = user
        else:
            session.clear()


def role_required(*roles):
    """Server-side access control. With no roles given, any signed-in user is allowed."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if g.user is None:
                flash("Please sign in to continue.", "info")
                return redirect(url_for("auth.login"))
            if session.get("must_change") and request.endpoint != "auth.change_password":
                return redirect(url_for("auth.change_password"))
            if roles and g.user["role"] not in roles:
                abort(403, "Your account type is not allowed to open this page.")
            return view(*args, **kwargs)
        return wrapper
    return decorator


def _too_many_failures(key):
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < config.LOGIN_WINDOW_SECONDS]
    _failures[key] = recent
    return len(recent) >= config.LOGIN_MAX_FAILS


def _check_password(user, password):
    """Returns (is_correct, credential_doc_or_None). None means 'still on the legacy password'."""
    cred = col("app_credentials").find_one({"userId": user["userId"]})
    if cred:
        return check_password_hash(cred["passwordHash"], password), cred
    legacy = user.get("password")
    if isinstance(legacy, str) and hmac.compare_digest(legacy.encode("utf-8"), password.encode("utf-8")):
        return True, None
    return False, None


# ---------------------------------------------------------------- routes
@auth_bp.route("/")
def index():
    if g.user:
        return redirect(url_for(HOME[g.user["role"]]))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for(HOME[g.user["role"]]))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        key = (request.remote_addr, email.lower())
        if not email or not password:
            flash("Enter your email and password.", "danger")
        elif len(password) > 128 or len(email) > 200:
            flash("Incorrect email or password.", "danger")
        elif _too_many_failures(key):
            flash("Too many wrong attempts. Please wait 10 minutes and try again.", "danger")
        else:
            user = col("users").find_one({"email": {"$regex": "^" + re.escape(email) + "$", "$options": "i"}})
            correct, cred = _check_password(user, password) if user else (False, None)
            if not correct:
                _failures.setdefault(key, []).append(time.time())
                flash("Incorrect email or password.", "danger")
            elif user.get("role") not in ALLOWED_ROLES:
                flash("This portal is for Admin, Teacher and Parent accounts only.", "warning")
            elif user.get("status") != "Active":
                flash("This account is not active. Please contact the school office.", "warning")
            else:
                must_change = bool(cred.get("mustChangePassword")) if cred else True
                if cred is None:   # first sign-in: save a hashed copy in our own collection
                    try:
                        col("app_credentials").insert_one({
                            "userId": user["userId"],
                            "passwordHash": generate_password_hash(password, method=HASH_METHOD),
                            "mustChangePassword": True, "createdAt": access.utc_now()})
                    except Exception as exc:   # two sign-ins at the same moment: the other one won
                        if type(exc).__name__ != "DuplicateKeyError":
                            raise
                session.clear()
                session["user_id"] = user["userId"]
                session["must_change"] = must_change
                if must_change:
                    flash("Welcome! For your security, please choose a new password now.", "info")
                    return redirect(url_for("auth.change_password"))
                return redirect(url_for(HOME[user["role"]]))
    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@role_required()
def change_password():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        cred = col("app_credentials").find_one({"userId": g.user["userId"]})
        if not cred or not check_password_hash(cred["passwordHash"], current):
            flash("Your current password is not correct.", "danger")
        elif len(new) < config.MIN_PASSWORD_LENGTH or len(new) > 128:
            flash("The new password must be %d to 128 characters long." % config.MIN_PASSWORD_LENGTH, "danger")
        elif new == current:
            flash("Choose a password that is different from your current one.", "danger")
        elif new != confirm:
            flash("The two new passwords do not match.", "danger")
        else:
            col("app_credentials").update_one(
                {"userId": g.user["userId"]},
                {"$set": {"passwordHash": generate_password_hash(new, method=HASH_METHOD),
                          "mustChangePassword": False, "changedAt": access.utc_now()}})
            session["must_change"] = False
            flash("Your password has been changed.", "success")
            return redirect(url_for(HOME[g.user["role"]]))
    return render_template("change_password.html")
