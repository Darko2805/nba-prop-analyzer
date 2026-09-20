from ..config import COMBO_PROP_TYPES
from ..data.databallr_client import fetch_team_data, find_player
from ..data.teamrankings_scraper import fetch_teamrankings_opponent_stats
from ..data.bbref_scraper import fetch_game_logs, fetch_shot_zone_profile, get_stat_from_games, GameLog
from ..data.bbref_league_stats import fetch_all_players_per_game, fetch_opponent_zone_defense
from ..data.models import PlayerStats, TeamProfile, OpponentDefense, PropPrediction
from ..data.team_mapping import normalize_team, fetch_back_to_back_teams, fetch_three_in_four_teams
from ..data import snapshot_store
from .matchup import calculate_matchup_factor
from .pace import calculate_pace_factor
from .shot_zone import calculate_shot_zone_exploitation
from .volume import calculate_volume_adjustment
from .free_throw import calculate_free_throw_factor
from .teammate_efficiency import calculate_teammate_efficiency_factor
from .rest_fatigue import calculate_rest_factor
from .trends import estimate_trend_factor, set_manual_trend, get_game_log_summary, _stat_label
from .probability import estimate_probability, classify_confidence


class PropAnalyzer:
    def __init__(self):
        self.players = []
        self.team_profiles = {}
        self.opponent_defenses = {}
        self.league_avg = {}
        self.tr_stats = {}

    def load_data(self):
        print("  Loading player stats snapshot...")
        self.players = snapshot_store.load_players()
        if self.players:
            print(f"  Loaded {len(self.players)} players from snapshot")
        else:
            print("  No snapshot found, fetching player stats live from basketball-reference...")
            try:
                self.players = fetch_all_players_per_game()
                print(f"  Loaded {len(self.players)} players")
            except Exception as e:
                print(f"  Player stats fetch failed ({e}), continuing with no player data")
                self.players = []

        print("  Fetching team stats from databallr...")
        try:
            self.team_profiles, self.opponent_defenses, self.league_avg = fetch_team_data()
            print(f"  Loaded {len(self.team_profiles)} teams")
        except Exception as e:
            print(f"  Team stats fetch failed ({e}), continuing with no team data")
            self.team_profiles, self.opponent_defenses, self.league_avg = {}, {}, {}

        print("  Fetching opponent stats from TeamRankings...")
        try:
            self.tr_stats = fetch_teamrankings_opponent_stats()
            print(f"  Loaded TeamRankings data for {len(self.tr_stats)} teams")
        except Exception as e:
            print(f"  TeamRankings scraping failed ({e}), continuing with databallr only")
            self.tr_stats = {}

        self._merge_teamrankings_data()

        print("  Loading opponent zone defense snapshot...")
        zone_defense = snapshot_store.load_opponent_zone_defense()
        if zone_defense:
            print(f"  Loaded zone defense for {len(zone_defense)} teams from snapshot")
        else:
            print("  No snapshot found, fetching opponent zone defense live from basketball-reference...")
            try:
                zone_defense = fetch_opponent_zone_defense()
            except Exception as e:
                print(f"  Opponent zone defense fetch failed ({e}), short/long mid-range gaps will use league average")
                zone_defense = {}
        self._merge_bbref_zone_defense(zone_defense)

    def is_ready(self) -> bool:
        """False when a required upstream data source failed to load."""
        return bool(self.players) and bool(self.team_profiles)

    def _merge_bbref_zone_defense(self, zone_defense: dict) -> None:
        """Fill in opponent short/long mid-range defense (at-rim and 3PT already come from databallr/TeamRankings)."""
        for abbr, zone in zone_defense.items():
            opp = self.opponent_defenses.get(abbr)
            if opp is None:
                continue
            opp.opp_short_mid_freq = zone["short_mid_freq"]
            opp.opp_short_mid_acc = zone["short_mid_acc"]
            opp.opp_long_mid_freq = zone["long_mid_freq"]
            opp.opp_long_mid_acc = zone["long_mid_acc"]

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
            if tr.get("opp_efg_pct"):
                opp.opp_efg_pct = tr["opp_efg_pct"]

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
                f"Try the exact name as shown on basketball-reference."
            )

        # Shot-zone frequency: snapshot first (this is what production relies on —
        # bbref blocks Render's IP), live fetch as a fallback for anyone not in the
        # rotation-player snapshot. On failure, leave zeros — shot_zone.py falls
        # back to estimating from three_point_rate.
        zone_profile = snapshot_store.load_shot_zone(player.name)
        if not zone_profile:
            try:
                zone_profile = fetch_shot_zone_profile(player.name)
            except Exception as e:
                print(f"  Shot-zone profile fetch failed ({e}), estimating from three-point rate")
                zone_profile = None
        if zone_profile:
            player.at_rim_freq = zone_profile["at_rim_freq"]
            player.short_mid_freq = zone_profile["short_mid_freq"]
            player.mid_range_freq = zone_profile["mid_range_freq"]

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

        # Game logs: snapshot first, live fetch as a fallback (see shot-zone note above).
        game_logs = snapshot_store.load_game_logs(player.name)
        if game_logs is not None:
            print(f"  Loaded {len(game_logs)} game logs from snapshot")
        else:
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

        # Get game log values for variance and trend (combo types sum the two
        # component stats per game, giving real joint variance/trend rather
        # than combining two separate marginal estimates)
        game_values = get_stat_from_games(game_logs, prop_type) if game_logs else None

        if prop_type in COMBO_PROP_TYPES:
            baseline, matchup_factor, matchup_note, pace_factor, pace_note, \
                zone_factor, zone_notes, zones, volume_factor, volume_note, \
                ft_factor, ft_note, teammate_factor, teammate_note, \
                rest_factor, rest_note, trend_factor, trend_note = \
                self._run_combo_factors(
                    player, player_team, opponent_team, opponent_defense, prop_type, game_logs
                )
        else:
            baseline = self._get_baseline(player, prop_type)
            matchup_factor, matchup_note = calculate_matchup_factor(
                player, player_team, opponent_defense, self.league_avg, prop_type,
                self.opponent_defenses, self.team_profiles,
            )
            pace_factor, pace_note = calculate_pace_factor(
                player_team, opponent_team, self.league_avg.get("pace", 100.0), self.team_profiles
            )
            zone_factor, zone_notes, zones = calculate_shot_zone_exploitation(
                player, player_team, opponent_defense, prop_type, self.opponent_defenses
            )
            volume_factor, volume_note = calculate_volume_adjustment(
                player, player_team, opponent_defense, self.league_avg, prop_type, self.opponent_defenses
            )
            ft_factor, ft_note = calculate_free_throw_factor(
                player, opponent_defense, prop_type, self.opponent_defenses
            )
            teammate_factor, teammate_note = calculate_teammate_efficiency_factor(
                player_team, prop_type, self.team_profiles
            )
            rest_factor, rest_note = calculate_rest_factor(
                player_team.abbreviation, opp_abbr, prop_type,
                fetch_back_to_back_teams(), fetch_three_in_four_teams(),
            )
            trend_factor, trend_note = estimate_trend_factor(
                player, prop_type, game_logs=game_logs
            )

        # Compute adjusted prediction
        adjusted = (
            baseline * matchup_factor * pace_factor * zone_factor * volume_factor
            * ft_factor * teammate_factor * rest_factor * trend_factor
        )

        # Probability (with real variance if available)
        over_prob, under_prob = estimate_probability(
            adjusted, prop_line, prop_type, game_values=game_values
        )
        confidence = classify_confidence(over_prob)

        # Collect factors: zone_notes is a list (multiple step lines)
        key_factors = [matchup_note, pace_note] + zone_notes + [volume_note, ft_note, teammate_note, rest_note, trend_note]

        after_volume = baseline * matchup_factor * pace_factor * zone_factor * volume_factor
        after_ft = after_volume * ft_factor
        after_teammate = after_ft * teammate_factor
        breakdown = {
            "baseline": baseline,
            "matchup": matchup_factor,
            "pace": pace_factor,
            "shot_zone": zone_factor,
            "volume": volume_factor,
            "free_throw": ft_factor,
            "teammate_efficiency": teammate_factor,
            "rest_fatigue": rest_factor,
            "trend": trend_factor,
            "after_matchup": baseline * matchup_factor,
            "after_pace": baseline * matchup_factor * pace_factor,
            "after_zone": baseline * matchup_factor * pace_factor * zone_factor,
            "after_volume": after_volume,
            "after_ft": after_ft,
            "after_teammate": after_teammate,
            "after_rest": after_teammate * rest_factor,
            "final": adjusted,
            # Each factor's own descriptive note, so the UI can show the real
            # reasoning behind whichever factor ends up headlining the
            # breakdown (see headline.py) instead of generic templated copy.
            "matchup_note": matchup_note,
            "pace_note": pace_note,
            "shot_zone_note": zone_notes[0] if zone_notes else "",
            "volume_note": volume_note,
            "free_throw_note": ft_note,
            "teammate_efficiency_note": teammate_note,
            "rest_fatigue_note": rest_note,
            "trend_note": trend_note,
        }
        if zones:
            breakdown["zones"] = zones

        # Add game log summary to breakdown if available
        if game_logs:
            summary = get_game_log_summary(game_logs, prop_type, n=10)
            if summary:
                breakdown["recent_games"] = summary

            vs_opp_games = [g for g in game_logs if normalize_team(g.opponent) == opp_abbr]
            vs_opp_vals = get_stat_from_games(vs_opp_games, prop_type)
            if vs_opp_vals:
                breakdown["vs_opponent"] = {
                    "avg": sum(vs_opp_vals) / len(vs_opp_vals),
                    "games": len(vs_opp_vals),
                }

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
        elif prop_type == "fgm":
            return player.fgm_pg
        elif prop_type == "fga":
            return player.fga_pg
        elif prop_type == "3pa":
            return player.three_pa_pg
        elif prop_type == "turnovers":
            return player.topg
        elif prop_type in COMBO_PROP_TYPES:
            return sum(self._get_baseline(player, comp) for comp in COMBO_PROP_TYPES[prop_type])
        else:
            raise ValueError(f"Unknown prop type: {prop_type}")

    def _run_combo_factors(self, player, player_team, opponent_team, opponent_defense, prop_type, game_logs):
        """
        Combo props (two-stat like pts_ast, or three-stat like pra) run each
        component through the exact same single-stat pipeline, then blend
        the factors weighted by each component's share of the combined
        baseline. This is what keeps a combo's blended factor honestly
        proportioned — e.g. shot-zone exploitation or the free-throw factor
        only speak to the scoring component, so their influence on PRA's
        combined number is naturally diluted to whatever share of PRA's
        baseline actually comes from points, rather than carrying their
        full points-calibrated strength into the combined stat. Pace is
        identical for every component (it doesn't depend on prop_type), so
        it isn't blended.
        """
        components = COMBO_PROP_TYPES[prop_type]
        baselines = [self._get_baseline(player, comp) for comp in components]
        baseline = sum(baselines)
        weights = [b / baseline if baseline > 0 else 1.0 / len(components) for b in baselines]

        pace_factor, pace_note = calculate_pace_factor(
            player_team, opponent_team, self.league_avg.get("pace", 100.0), self.team_profiles
        )

        matchup_factor = zone_factor = volume_factor = ft_factor = teammate_factor = trend_factor = 0.0
        matchup_parts, volume_parts, ft_parts, teammate_parts, trend_parts, zone_notes = [], [], [], [], [], []
        zones = None
        not_applicable = "not applicable"

        for comp, weight in zip(components, weights):
            label = _stat_label(comp)

            m, m_note = calculate_matchup_factor(
                player, player_team, opponent_defense, self.league_avg, comp,
                self.opponent_defenses, self.team_profiles,
            )
            z, z_notes, zn = calculate_shot_zone_exploitation(
                player, player_team, opponent_defense, comp, self.opponent_defenses
            )
            v, v_note = calculate_volume_adjustment(
                player, player_team, opponent_defense, self.league_avg, comp, self.opponent_defenses
            )
            f, f_note = calculate_free_throw_factor(
                player, opponent_defense, comp, self.opponent_defenses
            )
            te, te_note = calculate_teammate_efficiency_factor(
                player_team, comp, self.team_profiles
            )
            t, t_note = estimate_trend_factor(player, comp, game_logs=game_logs)

            matchup_factor += weight * m
            zone_factor += weight * z
            volume_factor += weight * v
            ft_factor += weight * f
            teammate_factor += weight * te
            trend_factor += weight * t

            matchup_parts.append(f"[{label}] {m_note}")
            volume_parts.append(f"[{label}] {v_note}")
            trend_parts.append(f"[{label}] {t_note}")
            if not_applicable not in f_note:
                ft_parts.append(f"[{label}] {f_note}")
            if not_applicable not in te_note:
                teammate_parts.append(f"[{label}] {te_note}")
            zone_notes += [f"[{label}] {n}" for n in z_notes if not_applicable not in n]
            zones = zones or zn

        matchup_note = " | ".join(matchup_parts)
        volume_note = " | ".join(volume_parts)
        trend_note = " | ".join(trend_parts)
        ft_note = " | ".join(ft_parts) if ft_parts else "~ Free-throw rate not applicable for this combo"
        teammate_note = " | ".join(teammate_parts) if teammate_parts else "~ Teammate shooting efficiency not applicable for this combo"
        if not zone_notes:
            zone_notes = ["~ Zone analysis not applicable for this combo"]

        # rest_fatigue only ever applies to "fgm", which is never a
        # component of any combo (pts_ast/pts_reb/ast_reb/pra are built
        # from points/rebounds/assists), so it's a direct no-op here
        # rather than looping a call that can never return anything else.
        rest_factor, rest_note = 1.0, "~ Rest/schedule effect not applicable for this combo"

        return (
            baseline, matchup_factor, matchup_note, pace_factor, pace_note,
            zone_factor, zone_notes, zones, volume_factor, volume_note,
            ft_factor, ft_note, teammate_factor, teammate_note,
            rest_factor, rest_note, trend_factor, trend_note,
        )
