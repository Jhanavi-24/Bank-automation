"""
Mock core-banking web app -- the proxy target for the automation system.

Deliberately built like the legacy internal tools described in the
assignment brief: server-rendered HTML, table-based layout, no test IDs,
no client-side framework, a stray iframe, and a handful of deterministic
"trigger" member IDs that reproduce the runtime conditions a replay engine
has to handle (not-found, permission-denied, transient errors, unexpected
interstitials, session expiry).

Run with:  python -m src.target_app.app  (defaults to http://127.0.0.1:5055)
"""
from __future__ import annotations

import time

from flask import Flask, redirect, render_template, request, session, url_for

from . import data

DEMO_USERNAME = "operator"
DEMO_PASSWORD = "demo-pass-1234"  # dummy demo credential, not a real secret


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "dev-only-secret-not-for-production"

    def require_login():
        return session.get("authenticated") is True

    @app.get("/")
    def index():
        if require_login():
            return redirect(url_for("search"))
        return redirect(url_for("login"))

    @app.get("/login")
    def login():
        return render_template("login.html", error=None)

    @app.post("/login")
    def login_submit():
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == DEMO_USERNAME and password == DEMO_PASSWORD:
            session.clear()
            session["authenticated"] = True
            return redirect(url_for("search"))
        return render_template("login.html", error="Invalid username or password."), 401

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/session-expired")
    def session_expired():
        session.clear()
        return render_template("session_expired.html")

    @app.get("/widgets/branch-notice")
    def branch_notice():
        return render_template("branch_notice.html")

    @app.get("/search")
    def search():
        if not require_login():
            return redirect(url_for("login"))
        return render_template("search.html", query=None, results=None)

    @app.post("/search")
    def search_submit():
        if not require_login():
            return redirect(url_for("login"))
        member_id = request.form.get("member_id", "").strip()

        if member_id == data.TRIGGER_SESSION_EXPIRED:
            session.clear()
            return redirect(url_for("session_expired"))

        # Render a one-row "results table" the way a legacy search screen
        # would, rather than redirecting straight to the detail page --
        # this is the search -> detail -> action shape the brief asks for.
        return render_template("search.html", query=member_id, results=[member_id])

    @app.get("/members/<member_id>")
    def member_detail(member_id: str):
        if not require_login():
            return redirect(url_for("login"))

        if member_id == data.TRIGGER_SESSION_EXPIRED:
            session.clear()
            return redirect(url_for("session_expired"))

        if member_id == data.TRIGGER_NOT_FOUND or (
            member_id not in data._MEMBERS and member_id not in data._RESTRICTED_IDS
        ):
            return render_template("member_not_found.html", member_id=member_id), 200

        if data.is_restricted(member_id):
            return render_template("permission_denied.html", member_id=member_id), 200

        if member_id == data.TRIGGER_TRANSIENT_ERROR:
            visits = session.get("transient_visits", {})
            count = visits.get(member_id, 0) + 1
            visits[member_id] = count
            session["transient_visits"] = visits
            if count == 1:
                return render_template("transient_error.html", member_id=member_id), 503

        if member_id == data.TRIGGER_INTERSTITIAL and not session.get(f"ack_{member_id}"):
            return render_template("interstitial.html", member_id=member_id)

        if member_id == data.TRIGGER_SUPERVISOR_OVERRIDE and not session.get(f"override_{member_id}"):
            return render_template("supervisor_override.html", member_id=member_id)

        member = data.get_member(member_id)
        return render_template("member_detail.html", member=member)

    @app.post("/members/<member_id>/ack")
    def member_ack(member_id: str):
        if not require_login():
            return redirect(url_for("login"))
        session[f"ack_{member_id}"] = True
        return redirect(url_for("member_detail", member_id=member_id))

    @app.post("/members/<member_id>/override")
    def member_override(member_id: str):
        if not require_login():
            return redirect(url_for("login"))
        session[f"override_{member_id}"] = True
        return redirect(url_for("member_detail", member_id=member_id))

    @app.get("/members/<member_id>/sub-accounts/new")
    def new_sub_account(member_id: str):
        if not require_login():
            return redirect(url_for("login"))
        member = data.get_member(member_id)
        if member is None:
            return render_template("member_not_found.html", member_id=member_id), 200
        return render_template("new_sub_account.html", member=member, error=None, form={})

    @app.post("/members/<member_id>/sub-accounts/new")
    def new_sub_account_submit(member_id: str):
        if not require_login():
            return redirect(url_for("login"))
        member = data.get_member(member_id)
        if member is None:
            return render_template("member_not_found.html", member_id=member_id), 200

        account_type = request.form.get("account_type", "")
        initial_deposit_raw = request.form.get("initial_deposit", "")
        try:
            initial_deposit = float(initial_deposit_raw)
        except ValueError:
            initial_deposit = None

        if account_type not in ("Savings", "Checking", "CD"):
            return render_template(
                "new_sub_account.html",
                member=member,
                error="Please choose a valid account type.",
                form=request.form,
            ), 200

        if initial_deposit is None or initial_deposit <= 0:
            return render_template(
                "new_sub_account.html",
                member=member,
                error="Initial deposit must be a number greater than 0.",
                form=request.form,
            ), 200

        return render_template(
            "confirm_sub_account.html",
            member=member,
            account_type=account_type,
            initial_deposit=initial_deposit,
        )

    @app.post("/members/<member_id>/sub-accounts/confirm")
    def confirm_sub_account(member_id: str):
        if not require_login():
            return redirect(url_for("login"))
        member = data.get_member(member_id)
        if member is None:
            return render_template("member_not_found.html", member_id=member_id), 200

        account_type = request.form.get("account_type", "")
        initial_deposit = float(request.form.get("initial_deposit", "0"))

        # Simulate a little processing latency, the way a real core system
        # posting a ledger entry would.
        time.sleep(0.3)

        acct = data.open_sub_account(member_id, account_type, initial_deposit)
        return render_template("sub_account_success.html", member=member, account=acct)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5055, debug=False)
