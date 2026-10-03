import fcntl
import json
import os
import sqlite3
from pathlib import Path


class State:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.lock_fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
        if not path.exists():
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(str(path))
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS scans (
                account TEXT PRIMARY KEY, completed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                account TEXT NOT NULL, id TEXT NOT NULL, mail TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', category TEXT,
                summary TEXT, deadline TEXT, delivered_at TEXT,
                PRIMARY KEY (account, id)
            );
            CREATE TABLE IF NOT EXISTS pushes (
                day TEXT PRIMARY KEY, sent_at TEXT NOT NULL
            );
        """)
        self.db.commit()

    def scan_time(self, account):
        row = self.db.execute("SELECT completed_at FROM scans WHERE account=?", (account,)).fetchone()
        return row[0] if row else None

    def add_messages(self, account, mails, scanned_at):
        with self.db:
            for mail in mails:
                self.db.execute(
                    "INSERT OR IGNORE INTO messages(account,id,mail) VALUES(?,?,?)",
                    (account, mail["id"], json.dumps(mail, ensure_ascii=False)),
                )
            self.db.execute(
                "INSERT INTO scans(account,completed_at) VALUES(?,?) "
                "ON CONFLICT(account) DO UPDATE SET completed_at=excluded.completed_at",
                (account, scanned_at),
            )

    def pending(self):
        rows = self.db.execute("SELECT account,id,mail FROM messages WHERE status='pending' ORDER BY json_extract(mail,'$.received')").fetchall()
        return [(row["account"], row["id"], json.loads(row["mail"])) for row in rows]

    def classify(self, account, message_id, result):
        with self.db:
            self.db.execute(
                "UPDATE messages SET status=?,category=?,summary=?,deadline=? WHERE account=? AND id=?",
                ("important" if result["important"] else "ignored", result.get("category"),
                 result.get("summary", ""), result.get("deadline", ""), account, message_id),
            )

    def undelivered(self):
        rows = self.db.execute(
            "SELECT account,id,mail,category,summary,deadline FROM messages "
            "WHERE status='important' AND delivered_at IS NULL ORDER BY json_extract(mail,'$.received')"
        ).fetchall()
        return [{"account": row["account"], "id": row["id"],
                 "mail": json.loads(row["mail"]), "category": row["category"],
                 "summary": row["summary"], "deadline": row["deadline"]}
                for row in rows]

    def pushed_today(self, day):
        return self.db.execute("SELECT 1 FROM pushes WHERE day=?", (day,)).fetchone() is not None

    def mark_sent(self, day, when, items):
        with self.db:
            self.db.execute("INSERT INTO pushes(day,sent_at) VALUES(?,?)", (day, when))
            for item in items:
                self.db.execute("UPDATE messages SET delivered_at=? WHERE account=? AND id=?",
                                (when, item["account"], item["id"]))

    def close(self):
        self.db.close()
        fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
        os.close(self.lock_fd)
