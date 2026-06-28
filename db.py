import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "tennis.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS players (
        user_id TEXT PRIMARY KEY,
        elo REAL NOT NULL DEFAULT 1200,
        age INTEGER,
        objectif TEXT,
        dispo TEXT,
        preference TEXT,
        matches_played INTEGER NOT NULL DEFAULT 0,
        wins INTEGER NOT NULL DEFAULT 0,
        losses INTEGER NOT NULL DEFAULT 0,
        win_streak INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS pending_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player1_id TEXT NOT NULL,
        player2_id TEXT NOT NULL,
        winner_id TEXT NOT NULL,
        score TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS saved_roles (
        user_id TEXT PRIMARY KEY,
        role_ids TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS match_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player1_id TEXT NOT NULL,
        player2_id TEXT NOT NULL,
        winner_id TEXT NOT NULL,
        score TEXT,
        elo1_before REAL,
        elo2_before REAL,
        elo1_after REAL,
        elo2_after REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()

    _add_column_if_missing(conn, "players", "niveau_ntrp", "TEXT")
    _add_column_if_missing(conn, "players", "dispo_semaine", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "players", "dispo_soirs", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "players", "dispo_weekend", "INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()

def _add_column_if_missing(conn, table, col, coltype):
    existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

def get_player(user_id: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM players WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return row

def ensure_player(user_id: str):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO players (user_id) VALUES (?)", (str(user_id),))
    conn.commit()
    conn.close()

def update_player_profile(
    user_id: str, age=None, objectif=None, preference=None,
    niveau_ntrp=None, dispo_semaine=None, dispo_soirs=None, dispo_weekend=None,
):
    ensure_player(user_id)
    conn = get_conn()
    fields, values = [], []
    if age is not None:
        fields.append("age = ?"); values.append(age)
    if objectif is not None:
        fields.append("objectif = ?"); values.append(objectif)
    if preference is not None:
        fields.append("preference = ?"); values.append(preference)
    if niveau_ntrp is not None:
        fields.append("niveau_ntrp = ?"); values.append(niveau_ntrp)
    if dispo_semaine is not None:
        fields.append("dispo_semaine = ?"); values.append(1 if dispo_semaine else 0)
    if dispo_soirs is not None:
        fields.append("dispo_soirs = ?"); values.append(1 if dispo_soirs else 0)
    if dispo_weekend is not None:
        fields.append("dispo_weekend = ?"); values.append(1 if dispo_weekend else 0)
    if fields:
        values.append(str(user_id))
        conn.execute(f"UPDATE players SET {', '.join(fields)} WHERE user_id = ?", values)
        conn.commit()
    conn.close()

def search_available(moment: str, exclude_user_id=None, preference=None):
    column = {"semaine": "dispo_semaine", "soirs": "dispo_soirs", "weekend": "dispo_weekend"}[moment]
    conn = get_conn()
    query = f"SELECT * FROM players WHERE {column} = 1"
    params = []
    if exclude_user_id is not None:
        query += " AND user_id != ?"
        params.append(str(exclude_user_id))
    if preference:
        query += " AND (preference = ? OR preference = 'les_deux' OR preference IS NULL)"
        params.append(preference)
    query += " ORDER BY elo DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows

def set_elo(user_id: str, elo: float):
    ensure_player(user_id)
    conn = get_conn()
    conn.execute("UPDATE players SET elo = ? WHERE user_id = ?", (elo, str(user_id)))
    conn.commit()
    conn.close()

def create_pending_match(player1_id, player2_id, winner_id, score):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO pending_matches (player1_id, player2_id, winner_id, score) VALUES (?, ?, ?, ?)",
        (str(player1_id), str(player2_id), str(winner_id), score),
    )
    conn.commit()
    match_id = cur.lastrowid
    conn.close()
    return match_id

def get_pending_match(match_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM pending_matches WHERE id = ?", (match_id,)).fetchone()
    conn.close()
    return row

def set_match_status(match_id: int, status: str):
    conn = get_conn()
    conn.execute("UPDATE pending_matches SET status = ? WHERE id = ?", (status, match_id))
    conn.commit()
    conn.close()

def record_match_result(player1_id, player2_id, winner_id, score, elo1_before, elo2_before, elo1_after, elo2_after):
    conn = get_conn()
    conn.execute(
        """INSERT INTO match_history
           (player1_id, player2_id, winner_id, score, elo1_before, elo2_before, elo1_after, elo2_after)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(player1_id), str(player2_id), str(winner_id), score, elo1_before, elo2_before, elo1_after, elo2_after),
    )

    for uid, won in ((player1_id, winner_id == str(player1_id)), (player2_id, winner_id == str(player2_id))):
        conn.execute("INSERT OR IGNORE INTO players (user_id) VALUES (?)", (str(uid),))
        if won:
            conn.execute(
                "UPDATE players SET matches_played = matches_played + 1, wins = wins + 1, win_streak = win_streak + 1 WHERE user_id = ?",
                (str(uid),),
            )
        else:
            conn.execute(
                "UPDATE players SET matches_played = matches_played + 1, losses = losses + 1, win_streak = 0 WHERE user_id = ?",
                (str(uid),),
            )
    conn.commit()
    conn.close()

def get_leaderboard(limit=10):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM players WHERE matches_played > 0 ORDER BY elo DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows

def get_match_history(user_id, limit=5):
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM match_history WHERE player1_id = ? OR player2_id = ?
           ORDER BY created_at DESC LIMIT ?""",
        (str(user_id), str(user_id), limit),
    ).fetchall()
    conn.close()
    return rows

def save_roles(user_id, role_ids):
    conn = get_conn()
    conn.execute(
        "INSERT INTO saved_roles (user_id, role_ids) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET role_ids = excluded.role_ids",
        (str(user_id), ",".join(str(r) for r in role_ids)),
    )
    conn.commit()
    conn.close()

def get_saved_roles(user_id):
    conn = get_conn()
    row = conn.execute("SELECT role_ids FROM saved_roles WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    if not row or not row["role_ids"]:
        return []
    return [int(r) for r in row["role_ids"].split(",") if r]
