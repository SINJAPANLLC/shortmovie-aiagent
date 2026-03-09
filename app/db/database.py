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
        CREATE TABLE IF NOT EXISTS admin_users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS dramas (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255),
            genre VARCHAR(100),
            theme TEXT,
            script TEXT,
            scene_count INTEGER DEFAULT 0,
            video_url VARCHAR(500),
            thumbnail_url VARCHAR(500),
            youtube_id VARCHAR(100),
            tiktok_id VARCHAR(100),
            views INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            status VARCHAR(50) DEFAULT 'draft',
            episode_number INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS ai_logs (
            id SERIAL PRIMARY KEY,
            drama_id INTEGER,
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


def get_all_dramas():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM dramas ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_drama_by_id(drama_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM dramas WHERE id = %s", (drama_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def create_drama(title, genre, theme, script="", status="draft", episode_number=1):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dramas (title, genre, theme, script, status, episode_number) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (title, genre, theme, script, status, episode_number)
    )
    drama_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return drama_id


def update_drama(drama_id: int, **kwargs):
    conn = get_connection()
    cur = conn.cursor()
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k} = %s")
        vals.append(v)
    vals.append(drama_id)
    cur.execute(f"UPDATE dramas SET {', '.join(sets)} WHERE id = %s", vals)
    conn.commit()
    cur.close()
    conn.close()


def get_next_episode_number(genre=None):
    conn = get_connection()
    cur = conn.cursor()
    if genre:
        cur.execute("SELECT COUNT(*) as cnt FROM dramas WHERE genre = %s", (genre,))
    else:
        cur.execute("SELECT COUNT(*) as cnt FROM dramas")
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


def get_published_dramas():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, youtube_id, tiktok_id FROM dramas
        WHERE (youtube_id IS NOT NULL OR tiktok_id IS NOT NULL) AND status = 'published'
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def save_ai_log(drama_id, step, prompt, response):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_logs (drama_id, step, prompt, response) VALUES (%s, %s, %s, %s)",
        (drama_id, step, prompt[:5000] if prompt else "", response[:10000] if response else "")
    )
    conn.commit()
    cur.close()
    conn.close()


def get_ai_logs(drama_id=None, limit=30):
    conn = get_connection()
    cur = conn.cursor()
    if drama_id:
        cur.execute(
            "SELECT * FROM ai_logs WHERE drama_id = %s ORDER BY created_at DESC LIMIT %s",
            (drama_id, limit)
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


def get_dramas_with_analytics():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, genre, theme, views, likes, youtube_id, tiktok_id, created_at
        FROM dramas
        WHERE youtube_id IS NOT NULL OR tiktok_id IS NOT NULL
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
