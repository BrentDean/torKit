"""Board integration tests using the runtime Flask stack, without Tor/network."""
from __future__ import annotations

import os
import stat
from io import BytesIO
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
from onionshare_cli.web.board_mode import BoardModeWeb

# Docker image stores application resources in /opt/torkit/cli.
TEMPLATES = "/opt/torkit/cli/onionshare_cli/resources/templates"
STATIC = "/opt/torkit/cli/onionshare_cli/resources/static"


class Settings:
    def get(self, group, name):
        return "Test Board"


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "private-board.sqlite3"
        os.environ["TORKIT_BOARD_DB"] = str(self.db_path)
        self.addCleanup(os.environ.pop, "TORKIT_BOARD_DB", None)
        self.app, self.mode = self.build_app()
        self.client = self.app.test_client()
        self.csrf = self.token(self.client)

    def build_app(self):
        app = Flask(
            "torkit-board-tests",
            template_folder=TEMPLATES,
            static_folder=STATIC,
        )
        app.secret_key = "only-for-tests"
        fake_web = SimpleNamespace(
            app=app, settings=Settings(), static_url_path="/static",
        )
        mode = BoardModeWeb(SimpleNamespace(), fake_web)
        return app, mode

    def token(self, client):
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as session:
            return session["_board_csrf"]

    def post(self, author="Alice", title="Hello", body="First message"):
        return self.client.post(
            "/post",
            data={"csrf": self.csrf, "author": author,
                  "title": title, "body": body},
        )

    def test_create_reply_persist_after_new_app_and_escape_html(self):
        first = self.post(body="<script>alert(1)</script>")
        self.assertEqual(first.status_code, 303)
        self.assertEqual(first.headers["Location"], "/thread/1")
        thread = self.client.get("/thread/1")
        self.assertEqual(thread.status_code, 200)
        self.assertIn(b"&lt;script&gt;", thread.data)
        self.assertNotIn(b"<script>alert(1)</script>", thread.data)
        reply = self.client.post(
            "/reply/1", data={"csrf": self.csrf, "author": "Bob",
                              "body": "Still here"},
        )
        self.assertEqual(reply.status_code, 303)
        self.assertIn(b"Still here", self.client.get("/thread/1").data)
        app_again, _ = self.build_app()
        self.assertIn(b"Still here", app_again.test_client().get("/thread/1").data)
        self.assertEqual(stat.S_IMODE(self.db_path.stat().st_mode), 0o600)

    def test_requires_csrf_and_validates_payloads(self):
        self.assertEqual(self.client.post("/post", data={
            "title": "X", "body": "Y",
        }).status_code, 403)
        self.assertEqual(self.client.post("/post", data={
            "csrf": "wrong", "title": "X", "body": "Y",
        }).status_code, 403)
        self.assertEqual(self.post(body=" ").status_code, 400)
        self.assertEqual(self.post(title=" ").status_code, 400)
        self.assertEqual(self.post(title="X" * 121).status_code, 400)
        self.assertEqual(self.post(body="X" * 5001).status_code, 400)
        self.assertEqual(self.client.post(
            "/reply/98765", data={"csrf": self.csrf, "body": "Missing"},
        ).status_code, 404)
        self.assertEqual(self.client.get("/thread/98765").status_code, 404)

    def test_threads_and_replies_have_older_pages(self):
        with self.mode._db() as db:
            db.executemany(
                "INSERT INTO posts(parent_id, author, title, body) "
                "VALUES(NULL, 'Tester', ?, 'Content')",
                [(f"Topic-{number}",) for number in range(52)],
            )
            db.executemany(
                "INSERT INTO posts(parent_id, author, title, body) "
                "VALUES(1, 'Tester', '', ?)",
                [(f"Reply-{number}",) for number in range(252)],
            )
            db.commit()
        home = self.client.get("/?sort=newest")
        self.assertIn(b"Topic-51", home.data)
        self.assertNotIn(b"Topic-0", home.data)
        self.assertIn(b"Older", home.data)
        older = self.client.get("/?page=2&sort=newest")
        self.assertIn(b"Topic-0", older.data)
        recent_replies = self.client.get("/thread/1")
        self.assertIn(b"Reply-251", recent_replies.data)
        self.assertNotIn(b"Reply-0", recent_replies.data)
        older_replies = self.client.get("/thread/1?page=2")
        self.assertIn(b"Reply-0", older_replies.data)
        self.assertEqual(self.client.get("/thread/1?page=0").status_code, 400)

    def test_literal_search_in_title_body_and_reply(self):
        self.post(title="Alpine work", body="A mountain diary")
        self.post(title="Other discussion", body="Chocolate")
        self.client.post(
            "/reply/1",
            data={"csrf": self.csrf, "author": "Bob", "body": "snow day"},
        )
        for query in ("Alpine", "mountain", "snow", "SNOW"):
            response = self.client.get("/?q=" + query)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Alpine work", response.data)
            self.assertNotIn(b"Other discussion", response.data)
        self.post(title="100% ready", body="Literal search")
        percent = self.client.get("/?q=100%25")
        self.assertIn(b"100% ready", percent.data)
        self.assertNotIn(b"Other discussion", percent.data)
        underscore = self.client.get("/?q=_")
        self.assertNotIn(b"Other discussion", underscore.data)
        for invalid in ("/?page=0", "/?page=201", "/?sort=anything",
                        "/?q=" + "x" * 121):
            self.assertEqual(self.client.get(invalid).status_code, 400)

    def test_default_activity_sort_and_replies_sort(self):
        self.post(title="Older thread")
        self.post(title="Newer thread")
        self.client.post(
            "/reply/1", data={
                "csrf": self.csrf, "author": "Bob", "body": "First reply",
            },
        )
        home = self.client.get("/")
        self.assertLess(home.data.index(b"Older thread"),
                        home.data.index(b"Newer thread"))
        newest = self.client.get("/?sort=newest")
        self.assertLess(newest.data.index(b"Newer thread"),
                        newest.data.index(b"Older thread"))
        popular = self.client.get("/?sort=replies")
        self.assertLess(popular.data.index(b"Older thread"),
                        popular.data.index(b"Newer thread"))
        self.assertIn(b"1 reply", home.data)
        self.assertIn(b"2", home.data)
        self.assertIn(b"board.js", home.data)

    def test_filter_pagination_retains_search_and_order(self):
        with self.mode._db() as db:
            db.executemany(
                "INSERT INTO posts(parent_id, author, title, body) "
                "VALUES(NULL, 'Tester', ?, 'Content')",
                [(f"Topic-A-{number}",) for number in range(52)],
            )
            db.commit()
        response = self.client.get("/?sort=newest&q=Topic-A")
        self.assertIn(b"Older", response.data)
        self.assertIn(b"q=Topic-A", response.data)
        self.assertIn(b"sort=newest", response.data)
        other = self.client.get("/?sort=newest&q=Topic-A&page=2")
        self.assertIn(b"Topic-A-0", other.data)

    def upload(self, path, name, content, body="Photo", **other):
        data = {
            "csrf": self.csrf, "author": "Alice", "title": "Attachments",
            "body": body,
            "attachment": (BytesIO(content), name, "application/octet-stream"),
        }
        data.update(other)
        return self.client.post(path, data=data, content_type="multipart/form-data")

    def test_inline_image_and_download_persist_with_reply(self):
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image" * 10
        first = self.upload("/post", "diagram.png", png, body="")
        self.assertEqual(first.status_code, 303)
        page = self.client.get("/thread/1")
        self.assertIn(b"Attached image", page.data)
        self.assertIn(b"/attachment/1", page.data)
        self.assertIn(b"/attachment/1/download", page.data)
        image = self.client.get("/attachment/1")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.data, png)
        self.assertEqual(image.mimetype, "image/png")
        self.assertIn("inline", image.headers["Content-Disposition"])
        self.assertEqual(image.headers["Cache-Control"], "no-store")
        self.assertEqual(image.headers["X-Content-Type-Options"], "nosniff")
        download = self.client.get("/attachment/1/download")
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertEqual(download.data, png)

        reply = self.upload("/reply/1", "plans.pdf", b"%PDF-1.4\nTest data")
        self.assertEqual(reply.status_code, 303)
        pdf = self.client.get("/attachment/2")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertIn("attachment", pdf.headers["Content-Disposition"])
        self.assertEqual(pdf.data, b"%PDF-1.4\nTest data")
        self.assertIn(b"plans.pdf", self.client.get("/thread/1").data)
        self.assertIn(b"Includes an attachment", self.client.get("/").data)

        new_app, _ = self.build_app()
        reloaded = new_app.test_client()
        self.assertEqual(reloaded.get("/attachment/1").data, png)
        self.assertIn(b"plans.pdf", reloaded.get("/thread/1").data)
        self.assertEqual(stat.S_IMODE(self.mode.uploads.stat().st_mode), 0o700)
        self.assertTrue(all(
            stat.S_IMODE(path.stat().st_mode) == 0o600
            for path in self.mode.uploads.iterdir()
        ))
        self.assertFalse(any("diagram" in path.name for path in self.mode.uploads.iterdir()))

    def test_text_and_zip_files_are_download_only(self):
        for filename, data in (
            ("notes.txt", b"Private text file\n"),
            ("archive.zip", b"PK\x03\x04" + b"content"),
        ):
            post = self.upload("/post", filename, data, body="")
            self.assertEqual(post.status_code, 303)
        for attachment_id, expected_mimetype in (
            (1, "text/plain"), (2, "application/zip"),
        ):
            response = self.client.get(f"/attachment/{attachment_id}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, expected_mimetype)
            self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_rejects_unsafe_extensions_and_forged_magic(self):
        for filename, content in (
            ("evil.html", b"<script>evil()</script>"),
            ("vector.svg", b"<svg></svg>"),
            ("diagram.png", b"<svg></svg>"),
            ("photo.jpg", b"not really jpeg"),
            ("garbled.txt", b"\xff\xfe"),
            ("empty.pdf", b""),
        ):
            response = self.upload("/post", filename, content)
            self.assertEqual(response.status_code, 415, filename)
            with self.mode._db() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
            self.assertEqual(list(self.mode.uploads.iterdir()) if self.mode.uploads.exists() else [], [])
        self.assertEqual(self.client.get("/attachment/999").status_code, 404)

    def test_file_size_and_board_quota_fail_without_orphans(self):
        too_big = self.upload(
            "/post", "too-big.txt", b"x" * (10 * 1024 * 1024 + 1),
        )
        self.assertEqual(too_big.status_code, 413)
        with patch("onionshare_cli.web.board_mode.MAX_ATTACHMENTS_TOTAL", 8):
            blocked = self.upload("/post", "small.txt", b"longer than 8")
        self.assertEqual(blocked.status_code, 507)
        with self.mode._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)
        self.assertEqual(list(self.mode.uploads.iterdir()) if self.mode.uploads.exists() else [], [])

    def test_csrf_blocks_file_upload_and_reply_to_missing_thread(self):
        request_data = {
            "title": "No CSRF", "body": "unauthorized",
            "attachment": (BytesIO(b"PK\x03\x04a"), "bad.zip"),
        }
        denied = self.client.post(
            "/post", data=request_data, content_type="multipart/form-data",
        )
        self.assertEqual(denied.status_code, 403)
        denied_reply = self.upload(
            "/reply/999", "missing.txt", b"Not accepted",
        )
        self.assertEqual(denied_reply.status_code, 404)
        self.assertFalse(self.mode.uploads.exists())
        with self.mode._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_multiple_files_rejected(self):
        denied = self.client.post(
            "/post",
            data={
                "csrf": self.csrf, "title": "Two files", "body": "",
                "attachment": [
                    (BytesIO(b"A"), "a.txt"),
                    (BytesIO(b"B"), "b.txt"),
                ],
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(denied.status_code, 400)
        self.assertFalse(self.mode.uploads.exists())

    def test_private_pages_are_not_cacheable(self):
        for path in ("/",):
            response = self.client.get(path)
            self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
