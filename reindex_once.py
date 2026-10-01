# 一次性迁移：清空法条类来源，重启后 seed_sources 按新的 条/章 切节规则重建
import sqlite3

conn = sqlite3.connect("/data/law-agent/law.sqlite3")
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("DELETE FROM sources WHERE title LIKE '法条 · %'")
conn.commit()
print("cleared sources:", conn.total_changes)
conn.close()
