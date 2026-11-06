"""One connection, explicit short transactions, no legacy migrations."""
import contextlib
import sqlite3
import threading
from pathlib import Path


class Database:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA busy_timeout=10000')
        version = self.connection.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            self.connection.close()
            raise RuntimeError(f'新内核仅支持 Schema 1，当前为 {version}；请选择新数据库')
        if version == 0:
            self.connection.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute('BEGIN IMMEDIATE')
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.connection.execute(sql, args)]

    def close(self):
        self.connection.close()
