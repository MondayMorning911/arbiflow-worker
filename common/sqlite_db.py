import aiosqlite
import os

DB_PATH = "video_tasks.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                mode TEXT,
                path TEXT,
                status TEXT DEFAULT 'ready'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS general_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                platform TEXT,
                video_path TEXT,
                scheduled_time TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        await db.commit()

async def save_task(user_id, mode, path):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tasks (user_id, mode, path) VALUES (?, ?, ?)",
            (user_id, mode, path)
        )
        await db.commit()

async def mark_ready(user_id, mode, path):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tasks (user_id, mode, path, status) VALUES (?, ?, ?, 'ready')",
            (user_id, mode, path)
        )
        await db.commit()

async def load_task(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, user_id, mode, path FROM tasks WHERE user_id = ? AND status = 'ready' LIMIT 1", (user_id,)) as cursor:
            return await cursor.fetchone()

async def get_ready_tasks():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, user_id, mode, path FROM tasks WHERE status = 'ready'") as cursor:
            return await cursor.fetchall()

async def delete_task(task_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()
