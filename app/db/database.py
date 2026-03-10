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

        CREATE TABLE IF NOT EXISTS series (
            id SERIAL PRIMARY KEY,
            series_number INTEGER DEFAULT 1,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            synopsis TEXT,
            total_episodes INTEGER DEFAULT 30,
            current_episode INTEGER DEFAULT 0,
            character_image VARCHAR(500),
            status VARCHAR(50) DEFAULT 'active',
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
            series_id INTEGER,
            series_episode INTEGER DEFAULT 1,
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

        CREATE TABLE IF NOT EXISTS characters (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            role VARCHAR(50) DEFAULT '主人公',
            description TEXT,
            voice_id VARCHAR(100),
            image_path VARCHAR(500),
            image_face VARCHAR(500),
            image_bust VARCHAR(500),
            image_fullbody VARCHAR(500),
            series_id INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'dramas' AND column_name = 'series_id'
            ) THEN
                ALTER TABLE dramas ADD COLUMN series_id INTEGER;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'dramas' AND column_name = 'series_episode'
            ) THEN
                ALTER TABLE dramas ADD COLUMN series_episode INTEGER DEFAULT 1;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'series' AND column_name = 'character_image'
            ) THEN
                ALTER TABLE series ADD COLUMN character_image VARCHAR(500);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'characters' AND column_name = 'image_face'
            ) THEN
                ALTER TABLE characters ADD COLUMN image_face VARCHAR(500);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'characters' AND column_name = 'image_bust'
            ) THEN
                ALTER TABLE characters ADD COLUMN image_bust VARCHAR(500);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'characters' AND column_name = 'image_fullbody'
            ) THEN
                ALTER TABLE characters ADD COLUMN image_fullbody VARCHAR(500);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'dramas' AND column_name = 'thumbnail_prompt'
            ) THEN
                ALTER TABLE dramas ADD COLUMN thumbnail_prompt TEXT;
            END IF;
        END $$;
    """)

    conn.commit()
    cur.close()
    conn.close()


def get_active_series():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM series WHERE status = 'active' ORDER BY created_at DESC LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def create_series(series_number, name, description="", synopsis="", total_episodes=30):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO series (series_number, name, description, synopsis, total_episodes) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (series_number, name, description, synopsis, total_episodes)
    )
    series_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return series_id


def update_series(series_id, **kwargs):
    conn = get_connection()
    cur = conn.cursor()
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k} = %s")
        vals.append(v)
    vals.append(series_id)
    cur.execute(f"UPDATE series SET {', '.join(sets)} WHERE id = %s", vals)
    conn.commit()
    cur.close()
    conn.close()


def get_next_series_number():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(series_number), 0) as max_num FROM series")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["max_num"] + 1


def get_all_series():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM series ORDER BY series_number DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_series_by_id(series_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM series WHERE id = %s", (series_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def get_dramas_by_series(series_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM dramas WHERE series_id = %s ORDER BY series_episode ASC", (series_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def delete_series(series_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM series WHERE id = %s", (series_id,))
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


def create_drama(title, genre, theme, script="", status="draft", episode_number=1, series_id=None, series_episode=1):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dramas (title, genre, theme, script, status, episode_number, series_id, series_episode) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (title, genre, theme, script, status, episode_number, series_id, series_episode)
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
        SELECT id, title, genre, theme, views, likes, youtube_id, tiktok_id, series_id, series_episode, created_at
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


def get_characters():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM characters ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_characters_by_series(series_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM characters WHERE series_id = %s OR series_id IS NULL ORDER BY created_at DESC", (series_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_character_by_id(character_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM characters WHERE id = %s", (character_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def create_character(name, role="主人公", description="", voice_id="", image_path="", series_id=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO characters (name, role, description, voice_id, image_path, series_id) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (name, role, description, voice_id, image_path, series_id)
    )
    char_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return char_id


def update_character(character_id, **kwargs):
    conn = get_connection()
    cur = conn.cursor()
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k} = %s")
        vals.append(v)
    vals.append(character_id)
    cur.execute(f"UPDATE characters SET {', '.join(sets)} WHERE id = %s", vals)
    conn.commit()
    cur.close()
    conn.close()


def delete_character(character_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM characters WHERE id = %s", (character_id,))
    conn.commit()
    cur.close()
    conn.close()
