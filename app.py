import os
from datetime import date, datetime
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import connect_db, create_tables

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("TABLEEASE_SECRET_KEY", "change-this-development-secret-before-deployment")
app.config["ADMIN_EMAIL"] = os.environ.get("TABLEEASE_ADMIN_EMAIL", "Nduvho&Nonka@gmail.com").strip().lower()
app.config["ADMIN_PASSWORD"] = os.environ.get("TABLEEASE_ADMIN_PASSWORD", "12345")

create_tables()


def customer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "customer_id" not in session:
            flash("Please log in before making a booking.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Please sign in as an administrator.", "error")
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please complete all fields.", "error")
        elif len(password) < 8:
            flash("Your password must contain at least 8 characters.", "error")
        else:
            try:
                with connect_db() as connection:
                    connection.execute(
                        "INSERT INTO customers (full_name, email, password) VALUES (?, ?, ?)",
                        (name, email, generate_password_hash(password)),
                    )
                flash("Your account has been created. You can now log in.", "success")
                return redirect(url_for("login"))
            except Exception as error:
                if "UNIQUE constraint failed" in str(error):
                    flash("That email address is already registered. Please log in instead.", "error")
                else:
                    app.logger.exception("Registration failed")
                    flash("We couldn't create your account. Please try again.", "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with connect_db() as connection:
            customer = connection.execute(
                "SELECT customer_id, full_name, email, password FROM customers WHERE email = ?",
                (email,),
            ).fetchone()
        if customer and check_password_hash(customer["password"], password):
            session.clear()
            session["customer_id"] = customer["customer_id"]
            session["customer_name"] = customer["full_name"]
            destination = request.args.get("next")
            if destination and destination.startswith("/") and not destination.startswith("//"):
                return redirect(destination)
            flash(f"Welcome back, {customer['full_name']}!", "success")
            return redirect(url_for("booking"))
        flash("Incorrect email or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


@app.route("/booking", methods=["GET", "POST"])
@customer_required
def booking():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        booking_date = request.form.get("date", "")
        booking_time = request.form.get("time", "")
        requests_text = request.form.get("requests", "").strip()
        try:
            guests = int(request.form.get("guests", "0"))
            selected_date = date.fromisoformat(booking_date)
            selected_time = datetime.strptime(booking_time, "%H:%M").time()
        except (ValueError, TypeError):
            flash("Please enter a valid date, time and number of guests.", "error")
            return render_template("booking.html", min_date=date.today().isoformat())

        if not phone or selected_date < date.today() or not (1 <= guests <= 6):
            flash("Please check your phone number, booking date and guest count (1–6).", "error")
        elif selected_time.hour < 10 or selected_time.hour >= 22:
            flash("Bookings are available between 10:00 and 21:00.", "error")
        else:
            with connect_db() as connection:
                reserved = connection.execute(
                    """SELECT COALESCE(SUM(guests), 0) AS total FROM bookings
                       WHERE booking_date = ? AND booking_time = ?
                       AND status IN ('Pending', 'Approved')""",
                    (booking_date, booking_time),
                ).fetchone()["total"]
                if reserved + guests > 30:
                    flash("Sorry, that time is full. Please choose another time.", "error")
                else:
                    connection.execute(
                        """INSERT INTO bookings
                           (customer_id, phone, booking_date, booking_time, guests, special_requests)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (session["customer_id"], phone, booking_date, booking_time, guests, requests_text),
                    )
                    flash("Your booking request has been submitted. It is awaiting confirmation.", "success")
                    return redirect(url_for("my_bookings"))
    return render_template("booking.html", min_date=date.today().isoformat())


@app.route("/my-bookings")
@customer_required
def my_bookings():
    with connect_db() as connection:
        bookings = connection.execute(
            "SELECT * FROM bookings WHERE customer_id = ? ORDER BY booking_date DESC, booking_time DESC",
            (session["customer_id"],),
        ).fetchall()
    return render_template("my_bookings.html", bookings=bookings)


@app.route("/booking/<int:booking_id>/cancel", methods=["POST"])
@customer_required
def cancel_booking(booking_id):
    with connect_db() as connection:
        result = connection.execute(
            "UPDATE bookings SET status = 'Cancelled' WHERE booking_id = ? AND customer_id = ? AND status IN ('Pending', 'Approved')",
            (booking_id, session["customer_id"]),
        )
        if result.rowcount:
            flash("Your booking has been cancelled.", "success")
        else:
            flash("That booking could not be cancelled.", "error")
    return redirect(url_for("my_bookings"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if email == app.config["ADMIN_EMAIL"] and password == app.config["ADMIN_PASSWORD"]:
            session.clear()
            session["is_admin"] = True
            session["admin_email"] = email
            return redirect(url_for("admin"))
        flash("Incorrect administrator email or password.", "error")
    return render_template("admin_login.html")


@app.route("/admin")
@admin_required
def admin():
    with connect_db() as connection:
        bookings = connection.execute(
            """SELECT b.*, c.full_name, c.email
               FROM bookings b JOIN customers c ON c.customer_id = b.customer_id
               ORDER BY b.booking_date DESC, b.booking_time DESC, b.booking_id DESC"""
        ).fetchall()
        totals = connection.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END) AS pending,
                      SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) AS approved
               FROM bookings"""
        ).fetchone()
    return render_template("admin.html", bookings=bookings, totals=totals)


@app.route("/admin/booking/<int:booking_id>/<action>", methods=["POST"])
@admin_required
def update_booking(booking_id, action):
    status_map = {"approve": "Approved", "reject": "Rejected", "cancel": "Cancelled"}
    new_status = status_map.get(action)
    if not new_status:
        flash("Unknown booking action.", "error")
        return redirect(url_for("admin"))
    with connect_db() as connection:
        result = connection.execute(
            "UPDATE bookings SET status = ? WHERE booking_id = ? AND status = 'Pending'",
            (new_status, booking_id),
        )
        if result.rowcount:
            flash(f"Booking #{booking_id} marked as {new_status.lower()}.", "success")
        else:
            flash("That booking is no longer pending.", "error")
    return redirect(url_for("admin"))


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    flash("Administrator signed out.", "success")
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
