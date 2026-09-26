"""Persistent, client-authorized TorKit message board.

Board is deliberately separate from disposable Chat. All Tor-authorized visitors
can read and post; there is no operator action exposed through this onion app.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from pathlib import Path

from flask import abort, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import HTTPException

MAX_TITLE = 120
MAX_NAME = 60
MAX_BODY = 5000
MAX_FILE = 10 * 1024 * 1024
MAX_REQUEST = MAX_FILE + 256 * 1024
MAX_ATTACHMENTS_TOTAL = 512 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
SUPPORTED = {
    ".jpg": ("image/jpeg", True),
    ".jpeg": ("image/jpeg", True),
    ".png": ("image/png", True),
    ".webp": ("image/webp", True),
    ".gif": ("image/gif", True),
    ".pdf": ("application/pdf", False),
    ".txt": ("text/plain", False),
    ".zip": ("application/zip", False),
}
MAX_THREADS = 50
MAX_REPLIES = 250
MAX_POSTS = 10000
MAX_SEARCH = 120
MAX_PAGE = 200
SORT_ORDERS = {
    "activity": "COALESCE(MAX(r.id), p.id) DESC, p.id DESC",
    "newest": "p.id DESC",
    "replies": "COUNT(r.id) DESC, p.id DESC",
}



class BoardFull(HTTPException):
    code = 507
    description = "Board attachment or post storage limit reached."


class BoardModeWeb:
    supports_file_requests = False

    def __init__(self, common, web):
        self.common = common
        self.web = web
        self.cur_history_id = 0
        default_db = Path.home() / ".torkit" / "state" / "board" / "board.sqlite3"
        self.path = Path(os.environ.get("TORKIT_BOARD_DB", str(default_db)))
        self.uploads = self.path.parent / "uploads"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self.web.app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST
        self._init_database()
        self.define_routes()

    def _db(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_database(self):
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
                    author TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT
                        (strftime('%Y-%m-%d %H:%M:%S', 'now'))
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS posts_parent_idx "
                "ON posts(parent_id, id)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL UNIQUE,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    is_image INTEGER NOT NULL
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS attachments_post_idx "
                "ON attachments(post_id)"
            )
            db.commit()
        os.chmod(self.path, 0o600)

    @staticmethod
    def _csrf():
        if "_board_csrf" not in session:
            session["_board_csrf"] = secrets.token_urlsafe(32)
        return session["_board_csrf"]

    @staticmethod
    def _validate_post():
        token = request.form.get("csrf", "")
        expected = session.get("_board_csrf", "")
        if not token or not expected or not hmac.compare_digest(token, expected):
            abort(403)
        if request.content_length is not None and request.content_length > MAX_REQUEST:
            abort(413)
        name = request.form.get("author", "").strip() or "Anonymous"
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        if (len(name) > MAX_NAME or len(title) > MAX_TITLE
                or len(body) > MAX_BODY or not body and not request.files.get("attachment")):
            abort(400)
        return name, title, body

    @staticmethod
    def _valid_signature(extension: str, head: bytes, content: bytes | None) -> bool:
        if extension in {".jpg", ".jpeg"}:
            return head.startswith(b"\xff\xd8\xff")
        if extension == ".png":
            return head.startswith(b"\x89PNG\r\n\x1a\n")
        if extension == ".gif":
            return head.startswith((b"GIF87a", b"GIF89a"))
        if extension == ".webp":
            return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
        if extension == ".pdf":
            return head.startswith(b"%PDF-")
        if extension == ".zip":
            return head.startswith((b"PK\x03\x04", b"PK\x05\x06"))
        if extension == ".txt" and content is not None:
            try:
                decoded = content.decode("utf-8")
            except UnicodeDecodeError:
                return False
            return not any(ord(ch) < 32 and ch not in "\r\n\t" for ch in decoded)
        return False

    def _store_upload(self, upload):
        """Validate and stream one file to a randomized, private path.

        Return metadata plus its path so a failed DB transaction can unlink it.
        This runs while holding a SQLite write lock to enforce the board quota.
        """
        if upload is None or not upload.filename:
            return None
        name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if (not name or len(name) > 180 or
                any(ord(ch) < 32 or ord(ch) == 127 for ch in name)):
            abort(400)
        extension = Path(name).suffix.lower()
        if extension not in SUPPORTED:
            abort(415)
        media_type, is_image = SUPPORTED[extension]
        self.uploads.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.uploads, 0o700)
        stored_name = secrets.token_hex(24) + extension
        destination = self.uploads / stored_name
        total = 0
        sha = hashlib.sha256()
        head = b""
        decoded_chunks = [] if extension == ".txt" else None
        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                while True:
                    chunk = upload.stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_FILE:
                        abort(413)
                    if len(head) < 16:
                        head += chunk[:16 - len(head)]
                    sha.update(chunk)
                    if decoded_chunks is not None:
                        decoded_chunks.append(chunk)
                    output.write(chunk)
            if not total or not self._valid_signature(
                    extension, head,
                    b"".join(decoded_chunks) if decoded_chunks is not None else None):
                abort(415)
            return (destination, name, stored_name, media_type, total,
                    sha.hexdigest(), int(is_image))
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def _attachments_for(db, post_ids):
        if not post_ids:
            return {}
        placeholders = ",".join("?" for _ in post_ids)
        rows = db.execute(
            "SELECT id, post_id, original_name, media_type, size_bytes, "
            "is_image FROM attachments WHERE post_id IN (" + placeholders + ")",
            post_ids,
        ).fetchall()
        return {row["post_id"]: row for row in rows}

    def _create_post(self, *, parent_id, author, title, body):
        upload_list = [file for file in request.files.getlist("attachment")
                       if file.filename]
        if len(upload_list) > 1:
            abort(400)
        uploaded = None
        try:
            with self._db() as db:
                # Prevent concurrent visitors from exceeding the board quota.
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] >= MAX_POSTS:
                    raise BoardFull()
                if upload_list:
                    uploaded = self._store_upload(upload_list[0])
                    size_in_use = db.execute(
                        "SELECT COALESCE(SUM(size_bytes), 0) FROM attachments"
                    ).fetchone()[0]
                    if size_in_use + uploaded[4] > MAX_ATTACHMENTS_TOTAL:
                        raise BoardFull()
                cur = db.execute(
                    "INSERT INTO posts(parent_id, author, title, body) "
                    "VALUES(?, ?, ?, ?)",
                    (parent_id, author, title, body),
                )
                if uploaded is not None:
                    db.execute(
                        "INSERT INTO attachments"
                        "(post_id, original_name, stored_name, media_type, "
                        "size_bytes, sha256, is_image) VALUES(?, ?, ?, ?, ?, ?, ?)",
                        (cur.lastrowid, *uploaded[1:]),
                    )
                db.commit()
                return cur.lastrowid
        except Exception:
            if uploaded is not None:
                uploaded[0].unlink(missing_ok=True)
            raise

    def define_routes(self):
        app = self.web.app

        @app.after_request
        def board_no_store(response):
            if request.path.startswith(("/thread/", "/post", "/reply")) or request.path == "/":
                response.headers["Cache-Control"] = "no-store"
            return response

        @app.get("/")
        def board_home():
            page = request.args.get("page", 1, type=int)
            query = request.args.get("q", "").strip()
            sort = request.args.get("sort", "activity")
            if (page < 1 or page > MAX_PAGE or len(query) > MAX_SEARCH
                    or sort not in SORT_ORDERS):
                abort(400)

            # instr() searches literal text, including % and _, without
            # interpreting user input as SQL LIKE wildcards.
            match = (
                " AND (instr(lower(p.title), lower(?)) > 0 "
                "OR instr(lower(p.body), lower(?)) > 0 "
                "OR EXISTS (SELECT 1 FROM posts sr WHERE sr.parent_id = p.id "
                "AND instr(lower(sr.body), lower(?)) > 0))"
            ) if query else ""
            params = (query, query, query) if query else ()
            with self._db() as db:
                threads = db.execute(
                    """SELECT p.id, p.author, p.title, p.body, p.created_at,
                              COUNT(r.id) AS replies,
                              COALESCE(MAX(r.created_at), p.created_at) AS latest_at
                       FROM posts p LEFT JOIN posts r ON r.parent_id = p.id
                       WHERE p.parent_id IS NULL"""
                    + match + " GROUP BY p.id ORDER BY " + SORT_ORDERS[sort]
                    + " LIMIT ? OFFSET ?",
                    (*params, MAX_THREADS + 1, (page - 1) * MAX_THREADS),
                ).fetchall()
                match_total = db.execute(
                    "SELECT COUNT(*) FROM posts p WHERE p.parent_id IS NULL"
                    + match, params,
                ).fetchone()[0]
                totals = db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(parent_id IS NOT NULL), 0) "
                    "FROM posts",
                ).fetchone()
                attachments = self._attachments_for(
                    db, [post["id"] for post in threads[:MAX_THREADS]],
                )
            return render_template(
                "board.html",
                title=self.web.settings.get("general", "title") or "Private board",
                threads=threads[:MAX_THREADS],
                attachments=attachments,
                page=page,
                has_older=len(threads) > MAX_THREADS,
                query=query,
                sort=sort,
                match_total=match_total,
                thread_total=totals[0] - totals[1],
                reply_total=totals[1],
                csrf=self._csrf(),
                static_url_path=self.web.static_url_path,
            )

        @app.post("/post")
        def board_post():
            author, title, body = self._validate_post()
            if not title:
                abort(400)
            post_id = self._create_post(
                parent_id=None, author=author, title=title, body=body,
            )
            return redirect(url_for("board_thread", thread_id=post_id), code=303)

        @app.get("/thread/<int:thread_id>")
        def board_thread(thread_id):
            page = request.args.get("page", 1, type=int)
            if page < 1 or page > MAX_PAGE:
                abort(400)
            with self._db() as db:
                thread = db.execute(
                    "SELECT * FROM posts WHERE id = ? AND parent_id IS NULL",
                    (thread_id,),
                ).fetchone()
                if thread is None:
                    abort(404)
                reply_total = db.execute(
                    "SELECT COUNT(*) FROM posts WHERE parent_id = ?",
                    (thread_id,),
                ).fetchone()[0]
                replies = db.execute(
                    "SELECT * FROM posts WHERE parent_id = ? "
                    "ORDER BY id DESC LIMIT ? OFFSET ?",
                    (thread_id, MAX_REPLIES + 1, (page - 1) * MAX_REPLIES),
                ).fetchall()
                attachments = self._attachments_for(
                    db, [thread_id] + [reply["id"] for reply in replies[:MAX_REPLIES]],
                )
            has_older = len(replies) > MAX_REPLIES
            return render_template(
                "board_thread.html",
                title=self.web.settings.get("general", "title") or "Private board",
                thread=thread,
                reply_total=reply_total,
                replies=replies[:MAX_REPLIES],
                attachments=attachments,
                page=page,
                has_older=has_older,
                csrf=self._csrf(),
                static_url_path=self.web.static_url_path,
            )

        @app.post("/reply/<int:thread_id>")
        def board_reply(thread_id):
            author, _title, body = self._validate_post()
            with self._db() as db:
                thread = db.execute(
                    "SELECT id FROM posts WHERE id = ? AND parent_id IS NULL",
                    (thread_id,),
                ).fetchone()
                if thread is None:
                    abort(404)
            self._create_post(
                parent_id=thread_id, author=author, title="", body=body,
            )
            return redirect(url_for("board_thread", thread_id=thread_id), code=303)

        @app.get("/attachment/<int:attachment_id>")
        def board_attachment(attachment_id):
            return serve_attachment(attachment_id, force_download=False)

        @app.get("/attachment/<int:attachment_id>/download")
        def board_download(attachment_id):
            return serve_attachment(attachment_id, force_download=True)

        def serve_attachment(attachment_id, force_download):
            with self._db() as db:
                attachment = db.execute(
                    "SELECT original_name, stored_name, media_type, is_image "
                    "FROM attachments WHERE id = ?",
                    (attachment_id,),
                ).fetchone()
            if attachment is None:
                abort(404)
            stored_name = attachment["stored_name"]
            if (not stored_name or "/" in stored_name or "\\" in stored_name
                    or stored_name in {".", ".."}):
                abort(404)
            path = self.uploads / stored_name
            if not path.is_file() or path.is_symlink():
                abort(404)
            inline = bool(attachment["is_image"]) and not force_download
            response = send_file(
                path, mimetype=attachment["media_type"],
                as_attachment=not inline,
                download_name=attachment["original_name"],
                conditional=False,
            )
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
            return response
