K_FACTOR = 36

def expected_score(rating_a: float, rating_b: float) -> float:
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

def update_elo(winner_elo: float, loser_elo: float):
    expected_winner = expected_score(winner_elo, loser_elo)
    expected_loser = expected_score(loser_elo, winner_elo)
    new_winner_elo = winner_elo + K_FACTOR * (1 - expected_winner)
    new_loser_elo = loser_elo + K_FACTOR * (0 - expected_loser)
    return round(new_winner_elo, 1), round(new_loser_elo, 1)

def estimate_starting_elo(years_playing: str, ntrp_self: str, frequency: str) -> float:
    """Estimation grossiere du niveau de depart a partir du questionnaire."""
    base = 600

    years_map = {"jamais": -50, "moins_1": -30, "1_3": 0, "3_10": 30, "plus_10": 50}
    ntrp_map = {"debutant": -400, "2.5_3": -200, "3.5": 0, "4.0": 200, "4.5_plus": 400}
    freq_map = {"jamais": -20, "rarement": -10, "regulierement": 10, "tres_souvent": 20}

    base += years_map.get(years_playing, 0)
    base += ntrp_map.get(ntrp_self, 0)
    base += freq_map.get(frequency, 0)

    return max(100, round(base))
