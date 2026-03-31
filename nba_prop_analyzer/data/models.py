from dataclasses import dataclass, field


@dataclass
class PlayerStats:
    name: str
    team_abbr: str
    position: str
    games_played: int
    mpg: float
    ppg: float
    apg: float
    rpg: float
    orpg: float
    drpg: float
    three_pm_pg: float
    three_pa_pg: float
    three_pct: float
    three_point_rate: float
    ft_rate: float
    fta_per100: float
    ts_pct: float
    tsa_per100: float
    offensive_archetype: str
    at_rim_freq: float
    mid_range_freq: float
    o_dpm: float
    d_dpm: float
    ortg_on: float
    drtg_on: float

    @property
    def pra(self) -> float:
        return self.ppg + self.rpg + self.apg


@dataclass
class TeamProfile:
    name: str
    abbreviation: str
    games_played: int
    pace: float
    ortg: float
    drtg: float
    ts_pct: float
    at_rim_freq: float
    at_rim_acc: float
    long_mid_freq: float
    long_mid_acc: float
    short_mid_freq: float
    short_mid_acc: float
    three_point_rate: float
    ppg: float
    rpg: float
    apg: float


@dataclass
class OpponentDefense:
    team_name: str
    abbreviation: str
    opp_ppg: float
    opp_fg_pct: float
    opp_3pm: float
    opp_3pa: float
    opp_3p_pct: float
    opp_ftm: float
    opp_fta: float
    opp_ft_pct: float
    opp_rpg: float
    opp_apg: float
    opp_topg: float
    opp_ts_pct: float
    opp_at_rim_freq: float
    opp_at_rim_acc: float
    drtg: float
    pace: float


@dataclass
class PropPrediction:
    player_name: str
    opponent: str
    prop_type: str
    prop_line: float
    season_avg: float
    predicted_value: float
    over_probability: float
    under_probability: float
    confidence: str
    key_factors: list[str] = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)
