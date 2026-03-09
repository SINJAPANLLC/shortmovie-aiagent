import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    db_url = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("No database URL configured")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255),
            theme VARCHAR(255),
            story TEXT,
            audio_url VARCHAR(500),
            video_url VARCHAR(500),
            youtube_id VARCHAR(100),
            views INTEGER DEFAULT 0,
            ctr DECIMAL(5,2) DEFAULT 0,
            watch_time DECIMAL(10,2) DEFAULT 0,
            status VARCHAR(50) DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS admin_users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS ai_logs (
            id SERIAL PRIMARY KEY,
            video_id INTEGER,
            step VARCHAR(100),
            prompt TEXT,
            response TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR(100) PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


def get_all_videos():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM videos ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_video_by_id(video_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM videos WHERE id = %s", (video_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def create_video(title, theme, story, status="draft"):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO videos (title, theme, story, status) VALUES (%s, %s, %s, %s) RETURNING id",
        (title, theme, story, status)
    )
    video_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return video_id


def update_video(video_id: int, **kwargs):
    conn = get_connection()
    cur = conn.cursor()
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k} = %s")
        vals.append(v)
    vals.append(video_id)
    cur.execute(f"UPDATE videos SET {', '.join(sets)} WHERE id = %s", vals)
    conn.commit()
    cur.close()
    conn.close()


def get_next_video_number():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM videos")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["cnt"] + 1


def get_admin_user(username: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_users WHERE username = %s", (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def create_admin_user(username: str, password_hash: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO admin_users (username, password_hash) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING",
        (username, password_hash)
    )
    conn.commit()
    cur.close()
    conn.close()


def get_videos_with_analytics():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, theme, views, ctr, watch_time, youtube_id, created_at
        FROM videos
        WHERE youtube_id IS NOT NULL
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def save_ai_log(video_id, step, prompt, response):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_logs (video_id, step, prompt, response) VALUES (%s, %s, %s, %s)",
        (video_id, step, prompt, response)
    )
    conn.commit()
    cur.close()
    conn.close()


def get_ai_logs(video_id=None, limit=50):
    conn = get_connection()
    cur = conn.cursor()
    if video_id:
        cur.execute(
            "SELECT * FROM ai_logs WHERE video_id = %s ORDER BY created_at DESC LIMIT %s",
            (video_id, limit)
        )
    else:
        cur.execute(
            "SELECT * FROM ai_logs ORDER BY created_at DESC LIMIT %s",
            (limit,)
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_published_videos():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, youtube_id FROM videos
        WHERE youtube_id IS NOT NULL AND status = 'published'
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_setting(key, default=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (key, str(value)))
    conn.commit()
    cur.close()
    conn.close()
