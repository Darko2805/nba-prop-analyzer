from ..data.databallr_client import fetch_all_players, fetch_team_data, find_player
from ..data.teamrankings_scraper import fetch_teamrankings_opponent_stats
from ..data.bbref_scraper import fetch_game_logs, get_stat_from_games, GameLog
from ..data.models import PlayerStats, TeamProfile, OpponentDefense, PropPrediction
from ..data.team_mapping import normalize_team
from .matchup import calculate_matchup_factor
from .pace import calculate_pace_factor
from .opponent_profile import calculate_opponent_profile_adjustment
from .volume import calculate_volume_adjustment
from .trends import estimate_trend_factor, set_manual_trend, get_game_log_summary
from .probability import estimate_probability, classify_confidence


class PropAnalyzer:
    def __init__(self):
        self.players = []
        self.team_profiles = {}
        self.opponent_defenses = {}
        self.league_avg = {}
        self.tr_stats = {}

    def load_data(self):
        print("  Fetching player stats from databallr...")
        self.players = fetch_all_players()
        print(f"  Loaded {len(self.players)} players")

        print("  Fetching team stats from databallr...")
        self.team_profiles, self.opponent_defenses, self.league_avg = fetch_team_data()
        print(f"  Loaded {len(self.team_profiles)} teams")

        print("  Fetching opponent stats from TeamRankings...")
        try:
            self.tr_stats = fetch_teamrankings_opponent_stats()
            print(f"  Loaded TeamRankings data for {len(self.tr_stats)} teams")
        except Exception as e:
            print(f"  TeamRankings scraping failed ({e}), continuing with databallr only")

        self._merge_teamrankings_data()

    def _merge_teamrankings_data(self):
        """Overwrite opponent defense fields with TeamRankings values (more accurate current-season data)."""
        for abbr, tr in self.tr_stats.items():
            if abbr not in self.opponent_defenses:
                continue
            opp = self.opponent_defenses[abbr]
            if tr.get("opp_ppg"):
                opp.opp_ppg = tr["opp_ppg"]
            if tr.get("opp_3pm"):
                opp.opp_3pm = tr["opp_3pm"]
            if tr.get("opp_3pa"):
                opp.opp_3pa = tr["opp_3pa"]
            if tr.get("opp_3p_pct"):
                opp.opp_3p_pct = tr["opp_3p_pct"]
            if tr.get("opp_rpg"):
                opp.opp_rpg = tr["opp_rpg"]
            if tr.get("opp_apg"):
                opp.opp_apg = tr["opp_apg"]
            if tr.get("opp_fta"):
                opp.opp_fta = tr["opp_fta"]
            if tr.get("opp_ftm"):
                opp.opp_ftm = tr["opp_ftm"]

    def analyze_prop(
        self,
        player_name,
        opponent_abbr,
        prop_type,
        prop_line,
        trend_override=1.0,
    ):
        # Resolve player
        player = find_player(player_name, self.players)
        if player is None:
            raise ValueError(
                f"Player '{player_name}' not found. "
                f"Try the exact name as shown on databallr."
            )

        # Resolve opponent
        opp_abbr = normalize_team(opponent_abbr)
        if not opp_abbr:
            raise ValueError(f"Unknown team abbreviation: '{opponent_abbr}'")

        player_team = self.team_profiles.get(player.team_abbr)
        if player_team is None:
            raise ValueError(f"Team profile not found for {player.team_abbr}")

        opponent_team = self.team_profiles.get(opp_abbr)
        if opponent_team is None:
            raise ValueError(f"Team profile not found for {opp_abbr}")

        opponent_defense = self.opponent_defenses.get(opp_abbr)
        if opponent_defense is None:
            raise ValueError(f"Opponent defense data not found for {opp_abbr}")

        # Set trend override
        if trend_override != 1.0:
            set_manual_trend(player.name, trend_override)

        # Fetch game logs from basketball-reference
        print(f"  Fetching game logs from basketball-reference for {player.name}...")
        game_logs = []
        try:
            game_logs = fetch_game_logs(player.name)
            if game_logs:
                print(f"  Loaded {len(game_logs)} game logs")
            else:
                print("  No game logs found (will use season averages)")
        except Exception as e:
            print(f"  Game log fetch failed ({e}), using season averages")

        # Get baseline stat
        baseline = self._get_baseline(player, prop_type)

        # Get game log values for variance and trend
        game_values = get_stat_from_games(game_logs, prop_type) if game_logs else None

        # Run all analysis modules
        matchup_factor, matchup_note = calculate_matchup_factor(
            player, player_team, opponent_defense, self.league_avg, prop_type
        )
        pace_factor, pace_note = calculate_pace_factor(
            player_team, opponent_team, self.league_avg.get("pace", 100.0)
        )
        opp_profile_factor, opp_note = calculate_opponent_profile_adjustment(
            player, opponent_defense, self.league_avg, prop_type
        )
        volume_factor, volume_note = calculate_volume_adjustment(
            player, player_team, opponent_defense, self.league_avg, prop_type
        )
        trend_factor, trend_note = estimate_trend_factor(
            player, prop_type, game_logs=game_logs
        )

        # Compute adjusted prediction
        adjusted = baseline * matchup_factor * pace_factor * opp_profile_factor * volume_factor * trend_factor

        # Probability (with real variance if available)
        over_prob, under_prob = estimate_probability(
            adjusted, prop_line, prop_type, game_values=game_values
        )
        confidence = classify_confidence(over_prob)

        # Collect factors
        key_factors = [matchup_note, pace_note, opp_note, volume_note, trend_note]

        breakdown = {
            "baseline": baseline,
            "matchup": matchup_factor,
            "pace": pace_factor,
            "opponent_profile": opp_profile_factor,
            "volume": volume_factor,
            "trend": trend_factor,
            "after_matchup": baseline * matchup_factor,
            "after_pace": baseline * matchup_factor * pace_factor,
            "after_opp": baseline * matchup_factor * pace_factor * opp_profile_factor,
            "after_volume": baseline * matchup_factor * pace_factor * opp_profile_factor * volume_factor,
            "final": adjusted,
        }

        # Add game log summary to breakdown if available
        if game_logs:
            summary = get_game_log_summary(game_logs, prop_type, n=5)
            if summary:
                breakdown["last_5_games"] = summary
            all_vals = get_stat_from_games(game_logs, prop_type)
            if all_vals:
                import math
                n = len(all_vals)
                avg = sum(all_vals) / n
                var = sum((v - avg) ** 2 for v in all_vals) / (n - 1) if n > 1 else 0
                breakdown["real_std_dev"] = math.sqrt(var)
                breakdown["real_season_avg"] = avg
                breakdown["games_played"] = n

        return PropPrediction(
            player_name=player.name,
            opponent=opp_abbr,
            prop_type=prop_type,
            prop_line=prop_line,
            season_avg=baseline,
            predicted_value=adjusted,
            over_probability=over_prob,
            under_probability=under_prob,
            confidence=confidence,
            key_factors=key_factors,
            breakdown=breakdown,
        )

    def _get_baseline(self, player, prop_type):
        if prop_type == "points":
            return player.ppg
        elif prop_type == "rebounds":
            return player.rpg
        elif prop_type == "assists":
            return player.apg
        elif prop_type == "3pm":
            return player.three_pm_pg
        elif prop_type == "pra":
            return player.pra
        else:
            raise ValueError(f"Unknown prop type: {prop_type}")
