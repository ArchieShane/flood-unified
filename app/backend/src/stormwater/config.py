"""环境配置。数据库凭据一律从 .env 读取，不进代码库（§4.1）。"""

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    dbname: str
    schema: str
    user: str
    password: str

    @property
    def conninfo(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password}"
        )

    def __repr__(self) -> str:  # 避免密码被日志/异常回溯打出来
        return f"DbSettings(host={self.host!r}, port={self.port}, dbname={self.dbname!r}, password=***)"


@lru_cache(maxsize=1)
def db_settings() -> DbSettings:
    missing = [k for k in ("PIPE_DB_HOST", "PIPE_DB_USER", "PIPE_DB_PASSWORD") if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            f"缺少环境变量 {missing} —— 请复制 .env.example 为 .env 并填写。"
        )
    return DbSettings(
        host=os.environ["PIPE_DB_HOST"],
        port=int(os.getenv("PIPE_DB_PORT", "35433")),
        dbname=os.getenv("PIPE_DB_NAME", "syq-pipe"),
        schema=os.getenv("PIPE_DB_SCHEMA", "dmt_irrigationstormwater_abudhabi"),
        user=os.environ["PIPE_DB_USER"],
        password=os.environ["PIPE_DB_PASSWORD"],
    )
