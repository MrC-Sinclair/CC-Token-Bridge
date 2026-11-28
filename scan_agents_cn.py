"""Read the dedicated local usage table in the Marvis desktop app."""

import hashlib
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path


@contextmanager
def _usage_db(root, filename, app):
    path = (Path(root) / filename).resolve()
    if not path.is_file():
        raise ValueError(f"{app} 用量数据库不存在，请选择包含 {filename} 的目录。")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.execute("PRAGMA query_only = ON")
            yield db
    except sqlite3.Error as exc:
        raise ValueError(f"无法读取 {app} 用量表：{exc}") from exc


def _counts(*values):
    if any(type(n) is not int or n < 0 for n in values):
        raise ValueError("用量表含有无效的 Token 数量。")


def scan_marvis(root):
    with _usage_db(root, "data.db", "Marvis") as db:
        for seq, convo, stamp, model, prompt, output, cached, total in db.execute("""
            SELECT id, conversation_id, created_at, model_id, input_tokens,
                   output_tokens, cached_tokens, total_tokens FROM llm_token_usage
        """):
            _counts(prompt, output, cached, total)
            if total != prompt + output or cached > prompt:
                raise ValueError("Marvis 报告的总量与输入、输出或缓存数量不一致。")
            if not total:
                continue
            # response_id groups many model calls. The usage table assigns its own
            # primary key to each one. Keep IDs independent of the database path.
            ident = hashlib.sha256(f"{convo}\0{stamp}\0{seq}".encode("utf-8")).hexdigest()
            yield dict(id=ident, timestamp=int(datetime.fromisoformat(stamp).timestamp()),
                       model=model or "unknown", input=prompt - cached, output=output,
                       cache_read=cached, cache_write=0)
