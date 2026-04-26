# config/db.py
# 数据库连接工厂：两个独立连接，tian-gong 只读采集，tg_monitor 监控写入
import os
import pymysql
from dotenv import load_dotenv

load_dotenv()


def get_source_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.getenv("SOURCE_HOST", "127.0.0.1"),
        port=int(os.getenv("SOURCE_PORT", "3306")),
        user=os.getenv("SOURCE_USER"),
        password=os.getenv("SOURCE_PASSWORD"),
        database=os.getenv("SOURCE_DATABASE"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def get_monitor_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.getenv("MONITOR_HOST", "127.0.0.1"),
        port=int(os.getenv("MONITOR_PORT", "3306")),
        user=os.getenv("MONITOR_USER"),
        password=os.getenv("MONITOR_PASSWORD"),
        database=os.getenv("MONITOR_DATABASE"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
