import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent))
DB_PATH = DATA_DIR / "tennis.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS players (
        user_id TEXT PRIMARY KEY,
        elo REAL NOT NULL DEFAULT 0,
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

    CREATE TABLE IF NOT EXISTS availability_ranges (
        user_id TEXT NOT NULL,
        day_of_week INTEGER NOT NULL,
        start_hour INTEGER NOT NULL,
        end_hour INTEGER NOT NULL,
        PRIMARY KEY (user_id, day_of_week)
    );

    CREATE TABLE IF NOT EXISTS reputation (
        user_id TEXT PRIMARY KEY,
        ponctuel INTEGER NOT NULL DEFAULT 0,
        agreable INTEGER NOT NULL DEFAULT 0,
        bon_niveau INTEGER NOT NULL DEFAULT 0,
        rejouerais INTEGER NOT NULL DEFAULT 0,
        total_ratings INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS weekly_challenge (
        user_id TEXT NOT NULL,
        week_key TEXT NOT NULL,
        PRIMARY KEY (user_id, week_key)
    );

    CREATE TABLE IF NOT EXISTS courts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        count INTEGER,
        lighting TEXT,
        free TEXT
    );

    CREATE TABLE IF NOT EXISTS court_presence (
        user_id TEXT PRIMARY KEY,
        court_id INTEGER NOT NULL,
        until_iso TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS kv_state (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    conn.commit()

    _add_column_if_missing(conn, "players", "niveau_ntrp", "TEXT")
    _add_column_if_missing(conn, "players", "instant_until", "TEXT")
    _add_column_if_missing(conn, "players", "last_checkin", "TEXT")
    _add_column_if_missing(conn, "players", "secteur", "TEXT")
    _add_column_if_missing(conn, "players", "referred_by", "TEXT")
    _add_column_if_missing(conn, "players", "xp", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "players", "presence_yes", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "players", "presence_total", "INTEGER NOT NULL DEFAULT 0")
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
    user_id: str, age=None, objectif=None, preference=None, niveau_ntrp=None, secteur=None,
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
    if secteur is not None:
        fields.append("secteur = ?"); values.append(secteur)
    if fields:
        values.append(str(user_id))
        conn.execute(f"UPDATE players SET {', '.join(fields)} WHERE user_id = ?", values)
        conn.commit()
    conn.close()

def set_day_range(user_id: str, day_of_week: int, start_hour: int, end_hour: int):
    ensure_player(user_id)
    conn = get_conn()
    conn.execute(
        "INSERT INTO availability_ranges (user_id, day_of_week, start_hour, end_hour) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, day_of_week) DO UPDATE SET start_hour = excluded.start_hour, end_hour = excluded.end_hour",
        (str(user_id), day_of_week, start_hour, end_hour),
    )
    conn.commit()
    conn.close()

def clear_day_range(user_id: str, day_of_week: int):
    conn = get_conn()
    conn.execute(
        "DELETE FROM availability_ranges WHERE user_id = ? AND day_of_week = ?", (str(user_id), day_of_week)
    )
    conn.commit()
    conn.close()

def get_day_range(user_id: str, day_of_week: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT start_hour, end_hour FROM availability_ranges WHERE user_id = ? AND day_of_week = ?",
        (str(user_id), day_of_week),
    ).fetchone()
    conn.close()
    return row

def get_all_ranges(user_id: str) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT day_of_week, start_hour, end_hour FROM availability_ranges WHERE user_id = ? ORDER BY day_of_week",
        (str(user_id),),
    ).fetchall()
    conn.close()
    return {r["day_of_week"]: (r["start_hour"], r["end_hour"]) for r in rows}

def set_instant_available(user_id: str, until_iso: str):
    ensure_player(user_id)
    conn = get_conn()
    conn.execute("UPDATE players SET instant_until = ? WHERE user_id = ?", (until_iso, str(user_id)))
    conn.commit()
    conn.close()

def clear_instant_available(user_id: str):
    ensure_player(user_id)
    conn = get_conn()
    conn.execute("UPDATE players SET instant_until = NULL WHERE user_id = ?", (str(user_id),))
    conn.commit()
    conn.close()

def search_recurring(day_of_week: int, hour: int = None, exclude_user_id=None, preference=None, secteur=None):
    conn = get_conn()
    query = (
        "SELECT DISTINCT p.* FROM players p JOIN availability_ranges a ON a.user_id = p.user_id "
        "WHERE a.day_of_week = ?"
    )
    params = [day_of_week]
    if hour is not None:
        query += " AND a.start_hour <= ? AND a.end_hour > ?"
        params.extend([hour, hour])
    if exclude_user_id is not None:
        query += " AND p.user_id != ?"
        params.append(str(exclude_user_id))
    if preference:
        query += " AND (p.preference = ? OR p.preference = 'les_deux' OR p.preference IS NULL)"
        params.append(preference)
    if secteur:
        query += " AND p.secteur = ?"
        params.append(secteur)
    query += " ORDER BY p.elo DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows

def search_instant(now_iso: str, exclude_user_id=None, preference=None, secteur=None):
    conn = get_conn()
    query = "SELECT * FROM players WHERE instant_until IS NOT NULL AND instant_until > ?"
    params = [now_iso]
    if exclude_user_id is not None:
        query += " AND user_id != ?"
        params.append(str(exclude_user_id))
    if preference:
        query += " AND (preference = ? OR preference = 'les_deux' OR preference IS NULL)"
        params.append(preference)
    if secteur:
        query += " AND secteur = ?"
        params.append(secteur)
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

def set_last_checkin(user_id, date_iso: str):
    ensure_player(user_id)
    conn = get_conn()
    conn.execute("UPDATE players SET last_checkin = ? WHERE user_id = ?", (date_iso, str(user_id)))
    conn.commit()
    conn.close()

REPUTATION_TAGS = ("ponctuel", "agreable", "bon_niveau", "rejouerais")

def add_reputation(user_id, tags: set):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO reputation (user_id) VALUES (?)", (str(user_id),)
    )
    increments = [f"{tag} = {tag} + 1" for tag in tags if tag in REPUTATION_TAGS]
    set_clause = ", ".join(increments + ["total_ratings = total_ratings + 1"])
    conn.execute(f"UPDATE reputation SET {set_clause} WHERE user_id = ?", (str(user_id),))
    conn.commit()
    conn.close()

def get_reputation(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM reputation WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return row

def get_match_dates(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT created_at FROM match_history WHERE player1_id = ? OR player2_id = ?",
        (str(user_id), str(user_id)),
    ).fetchall()
    conn.close()
    return [r["created_at"][:10] for r in rows]

def get_distinct_partners(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT player1_id, player2_id FROM match_history WHERE player1_id = ? OR player2_id = ?",
        (str(user_id), str(user_id)),
    ).fetchall()
    conn.close()
    uid = str(user_id)
    partners = set()
    for r in rows:
        partners.add(r["player2_id"] if r["player1_id"] == uid else r["player1_id"])
    return partners

def has_played_before(user_id, opponent_id):
    conn = get_conn()
    row = conn.execute(
        """SELECT COUNT(*) as c FROM match_history
           WHERE (player1_id = ? AND player2_id = ?) OR (player1_id = ? AND player2_id = ?)""",
        (str(user_id), str(opponent_id), str(opponent_id), str(user_id)),
    ).fetchone()
    conn.close()
    return row["c"] > 0

def get_initiated_count(user_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM match_history WHERE player1_id = ?", (str(user_id),)
    ).fetchone()
    conn.close()
    return row["c"]

def mark_weekly_challenge(user_id, week_key: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO weekly_challenge (user_id, week_key) VALUES (?, ?)",
        (str(user_id), week_key),
    )
    conn.commit()
    conn.close()

def has_weekly_challenge(user_id, week_key: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM weekly_challenge WHERE user_id = ? AND week_key = ?", (str(user_id), week_key)
    ).fetchone()
    conn.close()
    return row is not None

def get_leaderboard_by_matches(limit=10):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM players WHERE matches_played > 0 ORDER BY matches_played DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows

def get_leaderboard_by_reputation(limit=10):
    conn = get_conn()
    rows = conn.execute(
        """SELECT r.*, p.elo, p.niveau_ntrp FROM reputation r JOIN players p ON p.user_id = r.user_id
           WHERE r.total_ratings > 0 ORDER BY (CAST(r.rejouerais AS REAL) / r.total_ratings) DESC, r.total_ratings DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return rows

def get_all_active_players():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM players WHERE matches_played > 0").fetchall()
    conn.close()
    return rows

def get_all_players_with_secteur():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM players WHERE secteur IS NOT NULL").fetchall()
    conn.close()
    return rows

# --- Fiabilite (presence apres match) ---

def add_presence(user_id, came: bool):
    ensure_player(user_id)
    conn = get_conn()
    if came:
        conn.execute(
            "UPDATE players SET presence_yes = presence_yes + 1, presence_total = presence_total + 1 WHERE user_id = ?",
            (str(user_id),),
        )
    else:
        conn.execute(
            "UPDATE players SET presence_total = presence_total + 1 WHERE user_id = ?", (str(user_id),)
        )
    conn.commit()
    conn.close()

def get_reliability_pct(user_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT presence_yes, presence_total FROM players WHERE user_id = ?", (str(user_id),)
    ).fetchone()
    conn.close()
    if not row or not row["presence_total"]:
        return None
    return round(100 * row["presence_yes"] / row["presence_total"])

# --- Terrains ---

def add_court(name, count, lighting, free):
    conn = get_conn()
    conn.execute(
        "INSERT INTO courts (name, count, lighting, free) VALUES (?, ?, ?, ?)",
        (name, count, lighting, free),
    )
    conn.commit()
    conn.close()

def get_courts():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM courts ORDER BY name").fetchall()
    conn.close()
    return rows

def get_court(court_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM courts WHERE id = ?", (court_id,)).fetchone()
    conn.close()
    return row

def set_court_presence(user_id, court_id, until_iso):
    conn = get_conn()
    conn.execute(
        "INSERT INTO court_presence (user_id, court_id, until_iso) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET court_id = excluded.court_id, until_iso = excluded.until_iso",
        (str(user_id), court_id, until_iso),
    )
    conn.commit()
    conn.close()

def get_active_court_presence(now_iso):
    conn = get_conn()
    rows = conn.execute(
        "SELECT cp.*, c.name as court_name FROM court_presence cp JOIN courts c ON c.id = cp.court_id "
        "WHERE cp.until_iso > ? ORDER BY c.name",
        (now_iso,),
    ).fetchall()
    conn.close()
    return rows

# --- Parrainage / XP ---

def set_referred_by(user_id, referrer_id):
    ensure_player(user_id)
    conn = get_conn()
    conn.execute(
        "UPDATE players SET referred_by = ? WHERE user_id = ? AND referred_by IS NULL",
        (str(referrer_id), str(user_id)),
    )
    conn.commit()
    conn.close()

def add_xp(user_id, amount):
    ensure_player(user_id)
    conn = get_conn()
    conn.execute("UPDATE players SET xp = xp + ? WHERE user_id = ?", (amount, str(user_id)))
    conn.commit()
    conn.close()

def get_referral_count(user_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM players WHERE referred_by = ?", (str(user_id),)
    ).fetchone()
    conn.close()
    return row["c"]

def get_leaderboard_by_referrals(limit=10):
    conn = get_conn()
    rows = conn.execute(
        """SELECT referred_by as user_id, COUNT(*) as c FROM players
           WHERE referred_by IS NOT NULL GROUP BY referred_by ORDER BY c DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return rows

# --- KV state (pour timers d'automatisations: dernier mois annonce, etc.) ---

def get_kv(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM kv_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_kv(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO kv_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()

# --- Stats avancees ---

def get_favorite_partner(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT player1_id, player2_id FROM match_history WHERE player1_id = ? OR player2_id = ?",
        (str(user_id), str(user_id)),
    ).fetchall()
    conn.close()
    uid = str(user_id)
    counts = {}
    for r in rows:
        other = r["player2_id"] if r["player1_id"] == uid else r["player1_id"]
        counts[other] = counts.get(other, 0) + 1
    if not counts:
        return None, 0
    best = max(counts, key=counts.get)
    return best, counts[best]

def get_tiebreak_count(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT score FROM match_history WHERE (player1_id = ? OR player2_id = ?) AND score IS NOT NULL",
        (str(user_id), str(user_id)),
    ).fetchall()
    conn.close()
    return sum(1 for r in rows if "7-6" in (r["score"] or "") or "tb" in (r["score"] or "").lower())

def get_last_match_date(user_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT created_at FROM match_history WHERE player1_id = ? OR player2_id = ? ORDER BY created_at DESC LIMIT 1",
        (str(user_id), str(user_id)),
    ).fetchone()
    conn.close()
    return row["created_at"] if row else None

def get_monthly_match_counts(year_month: str):
    """year_month format 'YYYY-MM' -> {user_id: matches}"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT player1_id, player2_id FROM match_history WHERE created_at LIKE ?",
        (f"{year_month}%",),
    ).fetchall()
    conn.close()
    counts = {}
    for r in rows:
        counts[r["player1_id"]] = counts.get(r["player1_id"], 0) + 1
        counts[r["player2_id"]] = counts.get(r["player2_id"], 0) + 1
    return counts

def get_monthly_new_partner_counts(year_month: str):
    """Compte, pour le mois donne, combien de partenaires DIFFERENTS jamais affrontes avant ce mois chaque joueur a rencontres."""
    conn = get_conn()
    all_rows = conn.execute(
        "SELECT player1_id, player2_id, created_at FROM match_history ORDER BY created_at"
    ).fetchall()
    conn.close()
    seen = {}
    counts = {}
    for r in all_rows:
        a, b, ts = r["player1_id"], r["player2_id"], r["created_at"]
        is_target_month = ts.startswith(year_month)
        for me, other in ((a, b), (b, a)):
            seen.setdefault(me, set())
            if is_target_month and other not in seen[me]:
                counts[me] = counts.get(me, 0) + 1
            seen[me].add(other)
    return counts
