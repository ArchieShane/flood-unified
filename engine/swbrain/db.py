"""数据库连接。源库一律只读（会话级 default_transaction_read_only），输出库可写。"""
from __future__ import annotations
import tomllib
from pathlib import Path
import pandas as pd
import psycopg


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else Path(__file__).resolve().parents[1] / "config.toml"
    with open(p, "rb") as f:
        return tomllib.load(f)


def connect_source(cfg: dict, name: str, timeout_ms: int = 600_000) -> psycopg.Connection:
    s = cfg["source"][name]
    return psycopg.connect(
        host=s["host"], port=s["port"], user=s["user"], password=s["password"], dbname=s["dbname"],
        connect_timeout=20,
        options=f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms}",
    )


def connect_output(cfg: dict) -> psycopg.Connection:
    o = cfg["output"]
    return psycopg.connect(host=o["host"], port=o["port"], user=o["user"], password=o["password"],
                           dbname=o["dbname"], connect_timeout=20)


def df(conn: psycopg.Connection, sql: str, params=None) -> pd.DataFrame:
    """cursor → DataFrame，不经 SQLAlchemy。"""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    return pd.DataFrame.from_records(rows, columns=cols)
