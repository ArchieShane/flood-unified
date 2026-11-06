"""管网数据库只读访问（§4.1）。

这是 Esri SDE 导出的只读分析库：115 张表里我们只碰约 10 张，不上 ORM，
裸 SQL + Pydantic 出参即可。所有连接强制 read-only，防止误写生产数据。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import db_settings


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """开一个只读连接。"""
    s = db_settings()
    with psycopg.connect(s.conninfo, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("set search_path to %s, public" % s.schema)
        yield conn


def query(sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# §4.6 阶段一阻塞查询 —— 单位与数据质量确认
# ---------------------------------------------------------------------------

#: 查询 ①：pipeline 全 84 列。重点找雨水/灌溉区分字段、上下游节点字段、几何列 udt_name。
PIPELINE_COLUMNS_SQL = """
select ordinal_position, column_name, data_type, udt_name
from information_schema.columns
where table_schema = %s and table_name = 'pipeline'
order by ordinal_position
"""

#: 查询 ②：单位与数据质量确认（最高优先级）。
#: 判读规则 —— pipe_diameter 最大值约 2400 → mm；约 2.4 → m。
#:            gradient 范围 0.0005–0.05 → 十进制；100–2000 → 1:X 的 X。
PIPELINE_PROFILE_SQL = """
select count(*) as total,
       count(pipe_diameter) as dia_n,
       count(gradient) as grad_n,
       count(invert_level_upstream) as ivu_n,
       count(pipe_length) as len_n,
       count(pipe_material) as mat_n,
       min(pipe_diameter) as dia_min, max(pipe_diameter) as dia_max,
       min(gradient)      as grad_min, max(gradient)      as grad_max,
       min(pipe_length)   as len_min,  max(pipe_length)   as len_max
from pipeline
"""


def pipeline_columns() -> list[dict[str, Any]]:
    return query(PIPELINE_COLUMNS_SQL, (db_settings().schema,))


def pipeline_profile() -> dict[str, Any]:
    return query(PIPELINE_PROFILE_SQL)[0]
