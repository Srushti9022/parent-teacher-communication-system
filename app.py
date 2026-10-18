"""Smart School Portal - start here:  python app.py"""
import logging

from flask import Flask, g, render_template, request, session
from werkzeug.exceptions import HTTPException

import auth
import config
import grading
from db import col

NAV = {
    "Admin": [("admin.dashboard", "Dashboard"), ("admin.users", "Users"), ("admin.students", "Students"),
              ("admin.teachers", "Teachers"), ("admin.classes", "Classes"), ("admin.notifications", "Notifications")],
    "Teacher": [("teacher.dashboard", "Dashboard"), ("teacher.students", "Students"), ("teacher.marks", "Marks"),
                ("teacher.attendance", "Attendance"), ("messages.inbox", "Messages")],
    "Parent": [("parent.dashboard", "Dashboard"), ("parent.marks", "Marks"), ("parent.attendance", "Attendance"),
               ("parent.notifications", "Notifications"), ("messages.inbox", "Messages")],
}
DB_DOWN_ERRORS = {"ServerSelectionTimeoutError", "AutoReconnect", "ConnectionFailure", "NetworkTimeout"}


def create_app(test_config=None):
    from admin import bp as admin_bp
    from messages import bp as messages_bp
    from parent import bp as parent_bp
    from teacher import bp as teacher_bp

    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    if test_config:
        app.config.update(test_config)
    key = app.config["SECRET_KEY"]
    if not key or key.startswith("change-me"):
        raise RuntimeError("SECRET_KEY is missing. Copy .env.example to .env and put a long random value in SECRET_KEY (see README).")

    for blueprint in (auth.auth_bp, admin_bp, teacher_bp, parent_bp, messages_bp):
        app.register_blueprint(blueprint)

    @app.before_request
    def before_request():
        if request.endpoint == "static":
            return None
        auth.load_user()
        if request.method == "POST":
            auth.check_csrf()
        return None

    @app.after_request
    def security_headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store" if request.endpoint != "static" else "public, max-age=3600"
        return response

    @app.context_processor
    def template_globals():
        user = getattr(g, "user", None)
        unread = 0
        if user and user["role"] in ("Teacher", "Parent") and not session.get("must_change"):
            unread = col("messages").count_documents({"receiverId": user["userId"], "status": "Sent"})
        return {"csrf_token": auth.csrf_token, "nav_items": NAV.get(user["role"], []) if user else [],
                "unread_count": unread}

    @app.template_filter("grade")
    def grade_letter(percentage):
        return grading.grade_for(percentage)

    @app.template_filter("d")
    def format_date(value):
        return value.strftime("%d %b %Y") if value else ""

    @app.template_filter("dt")
    def format_datetime(value):
        if not value:
            return ""
        return value.strftime("%d %b %Y") if (value.hour, value.minute) == (0, 0) else value.strftime("%d %b %Y, %H:%M")

    @app.errorhandler(Exception)
    def handle_error(error):
        if isinstance(error, HTTPException):
            code, text = error.code, error.description
        elif type(error).__name__ in DB_DOWN_ERRORS:
            code, text = 503, "The portal cannot reach MongoDB. Check that MongoDB is running and that MONGO_URI in .env is correct."
        else:
            app.logger.exception("Unexpected error")
            code, text = 500, "Something went wrong on our side. Please try again."
        titles = {400: "Request problem", 403: "Not allowed", 404: "Page not found", 503: "Database unavailable"}
        return render_template("error.html", code=code, title=titles.get(code, "Error"), text=text), code

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_app().run(host="127.0.0.1", port=5000, debug=False)
