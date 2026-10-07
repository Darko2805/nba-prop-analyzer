from __future__ import annotations

import time

from ..config import COMBO_PROP_TYPES, CURRENT_SEASON_YEAR, PRIOR_SEASON_YEAR, PLAYER_BLEND_K, ROOKIE_BLEND_K
from ..data.databallr_client import fetch_team_data, find_player
from ..data.teamrankings_scraper import fetch_teamrankings_opponent_stats
from ..data.bbref_scraper import fetch_game_logs, fetch_shot_zone_profile, get_stat_from_games, GameLog
from ..data.bbref_league_stats import fetch_all_players_per_game, fetch_opponent_zone_defense
from ..data.models import PlayerStats, TeamProfile, OpponentDefense, PropPrediction
from ..data.team_mapping import normalize_team, fetch_current_teams, current_team_of, fetch_back_to_back_teams, fetch_three_in_four_teams
from ..data import snapshot_store
from ..data.season_blend import blend_weight, blend_dataclass, blend_numbers, league_average_player
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
        self.tr_stats_prev = {}
        self.team_weights = {}
        self._teams_checked_at = 0.0

    def load_data(self):
        print("  Loading player stats snapshot...")
        current_players = snapshot_store.load_players()
        prior_players = snapshot_store.load_players(prev=True)
        if not current_players and not prior_players:
            print("  No snapshot found, fetching player stats live from basketball-reference...")
            try:
                prior_players = fetch_all_players_per_game(PRIOR_SEASON_YEAR)
                current_players = fetch_all_players_per_game(CURRENT_SEASON_YEAR)
            except Exception as e:
                print(f"  Player stats fetch failed ({e}), continuing with no player data")
        self.players = self._blend_players(current_players, prior_players)
        if self.players:
            print(f"  Loaded {len(self.players)} players "
                  f"({len(current_players)} with new-season games, blended with last season)")

        print("  Fetching team stats from databallr...")
        try:
            self.team_profiles, self.opponent_defenses, self.league_avg, self.team_weights = fetch_team_data()
            print(f"  Loaded {len(self.team_profiles)} teams")
        except Exception as e:
            print(f"  Team stats fetch failed ({e}), continuing with no team data")
            self.team_profiles, self.opponent_defenses, self.league_avg, self.team_weights = {}, {}, {}, {}

        print("  Fetching opponent stats from TeamRankings...")
        self.tr_stats_prev = snapshot_store.load_teamrankings(prev=True)
        try:
            tr_stats, tr_label = fetch_teamrankings_opponent_stats()
            # TeamRankings names a season by the year it starts in, and its
            # "current" column is last season's until the new one begins.
            self.tr_stats = tr_stats if tr_label == str(CURRENT_SEASON_YEAR - 1) else {}
            if not self.tr_stats_prev and tr_label == str(PRIOR_SEASON_YEAR - 1):
                self.tr_stats_prev = tr_stats
            print(f"  Loaded TeamRankings data for {len(tr_stats)} teams (season column {tr_label!r}, "
                  f"{'new season' if self.tr_stats else 'last season, used as the prior'})")
        except Exception as e:
            print(f"  TeamRankings scraping failed ({e}), continuing with databallr only")
            self.tr_stats = {}

        self._merge_teamrankings_data()

        print("  Loading opponent zone defense snapshot...")
        zone_current = snapshot_store.load_opponent_zone_defense()
        zone_prior = snapshot_store.load_opponent_zone_defense(prev=True)
        if not zone_current and not zone_prior:
            print("  No snapshot found, fetching opponent zone defense live from basketball-reference...")
            try:
                zone_prior = fetch_opponent_zone_defense(PRIOR_SEASON_YEAR)
                zone_current = fetch_opponent_zone_defense(CURRENT_SEASON_YEAR)
            except Exception as e:
                print(f"  Opponent zone defense fetch failed ({e}), short/long mid-range gaps will use league average")
        else:
            print(f"  Loaded zone defense for {len(zone_current or zone_prior)} teams from snapshot")
        self._merge_bbref_zone_defense(zone_current, zone_prior)

        # Raw bbref team codes (e.g. "BRK") normalize to our canonical abbreviations.
        for p in self.players:
            p.team_abbr = normalize_team(p.team_abbr) or p.team_abbr
        self.refresh_player_teams(force=True)

    def refresh_player_teams(self, force: bool = False, max_age_seconds: int = 6 * 3600) -> None:
        """
        Points each player at the team they are on NOW (live ESPN rosters)
        instead of the team in the season-stats snapshot, which is last
        season's. Cheap to call on every request: it only does work when the
        last successful check is older than max_age_seconds, so mid-season
        trades are picked up without a redeploy. Players ESPN doesn't list
        keep their snapshot team.
        """
        now = time.time()
        if not force and now - self._teams_checked_at < max_age_seconds:
            return
        if not fetch_current_teams():
            if force:
                print("  Live rosters unavailable, keeping snapshot teams")
            self._teams_checked_at = now - max_age_seconds + 600  # retry in ~10 min
            return
        moved = 0
        for p in self.players:
            current = current_team_of(p.name)
            if current and current != p.team_abbr:
                p.team_abbr = current
                moved += 1
        self._teams_checked_at = now
        if force:
            print(f"  Updated the team for {moved} players from live rosters")

    def is_ready(self) -> bool:
        """False when a required upstream data source failed to load."""
        return bool(self.players) and bool(self.team_profiles)

    def _blend_players(self, current: list, prior: list) -> list:
        """Each player's numbers mix this season with last by games/(games+K); a player with no new-season
        games is exactly last season's numbers. A player with no last-season row (a rookie) mixes with a
        league-average player of their own minutes instead, with the smaller ROOKIE_BLEND_K."""
        by_name = {}
        for p in prior:
            p.blend_weight = 0.0
            by_name[p.name] = p
        for c in current:
            prev = by_name.get(c.name)
            if prev:
                w = blend_weight(c.games_played, PLAYER_BLEND_K)
                blended = blend_dataclass(c, prev, w)
            else:
                baseline = league_average_player(prior, c)
                w = blend_weight(c.games_played, ROOKIE_BLEND_K) if baseline else 1.0
                blended = blend_dataclass(c, baseline, w)
            blended.blend_weight = w
            by_name[c.name] = blended
        return list(by_name.values())

    def _league_zone(self) -> dict:
        """League-average shot-zone profile from last season, the starting point for a player with no zone
        history of their own."""
        zones = snapshot_store.load_shot_zones(prev=True).values()
        keys = {k for z in zones for k, v in z.items() if isinstance(v, (int, float))}
        return {k: sum(z[k] for z in zones if k in z) / sum(1 for z in zones if k in z) for k in keys} if zones else {}

    def _merge_bbref_zone_defense(self, current: dict, prior: dict) -> None:
        """Fill in opponent short/long mid-range defense (at-rim and 3PT already come from databallr/TeamRankings)."""
        for abbr in set(current) | set(prior):
            opp = self.opponent_defenses.get(abbr)
            if opp is None:
                continue
            zone = blend_numbers(current.get(abbr, {}), prior.get(abbr, {}), self.team_weights.get(abbr, 0.0))
            opp.opp_short_mid_freq = zone["short_mid_freq"]
            opp.opp_short_mid_acc = zone["short_mid_acc"]
            opp.opp_long_mid_freq = zone["long_mid_freq"]
            opp.opp_long_mid_acc = zone["long_mid_acc"]

    def _merge_teamrankings_data(self):
        """Overwrite opponent defense fields with TeamRankings values (more accurate than databallr), mixing the
        new season's with last season's by the team's games-played weight; with no new-season games it is
        exactly last season's TeamRankings numbers."""
        fields = ("opp_ppg", "opp_3pm", "opp_3pa", "opp_3p_pct", "opp_rpg", "opp_apg", "opp_fta", "opp_ftm", "opp_efg_pct")
        for abbr in set(self.tr_stats) | set(self.tr_stats_prev):
            opp = self.opponent_defenses.get(abbr)
            if opp is None:
                continue
            current = self.tr_stats.get(abbr, {})
            prior = self.tr_stats_prev.get(abbr, {})
            w = self.team_weights.get(abbr, 0.0) if prior else 1.0
            for field in fields:
                cv, pv = current.get(field), prior.get(field)
                if cv and pv:
                    value = w * cv + (1 - w) * pv
                else:
                    value = cv or pv
                if value:
                    setattr(opp, field, value)

    def _shot_zone_for(self, player) -> dict | None:
        """Shot-zone frequencies mixed across seasons by the player's own blend weight. Snapshots first
        (what production relies on -- bbref blocks the host's IP), a live fetch of last season only as a
        fallback for anyone not in the rotation snapshot; None leaves zeros so shot_zone.py falls back to
        estimating from three_point_rate."""
        current = snapshot_store.load_shot_zone(player.name)
        prior = snapshot_store.load_shot_zone(player.name, prev=True)
        if not current and not prior:
            try:
                prior = fetch_shot_zone_profile(player.name, PRIOR_SEASON_YEAR)
            except Exception as e:
                print(f"  Shot-zone profile fetch failed ({e}), estimating from three-point rate")
        if current and not prior:
            prior = self._league_zone()
        if current and prior:
            return blend_numbers(current, prior, player.blend_weight)
        return current or prior or None

    def _game_logs_for(self, player) -> list:
        """Last season's games followed by this season's, oldest first, so recent form (L5/L10/trend), hit
        rates and head-to-head history are meaningful from the first game of a new season."""
        prior = snapshot_store.load_game_logs(player.name, prev=True)
        current = snapshot_store.load_game_logs(player.name)
        if prior is None and current is None:
            print(f"  Fetching game logs from basketball-reference for {player.name}...")
            try:
                prior = fetch_game_logs(player.name, PRIOR_SEASON_YEAR)
                current = fetch_game_logs(player.name)
            except Exception as e:
                print(f"  Game log fetch failed ({e}), using season averages")
        logs = (prior or []) + (current or [])
        if not logs:
            print("  No game logs found (will use season averages)")
        return logs

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
        zone_profile = self._shot_zone_for(player)
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
        game_logs = self._game_logs_for(player)

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
