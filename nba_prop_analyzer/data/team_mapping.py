TEAM_ABBR_MAP = {
    "Atlanta Hawks": "ATL", "Hawks": "ATL", "ATL": "ATL",
    "Boston Celtics": "BOS", "Celtics": "BOS", "BOS": "BOS",
    "Brooklyn Nets": "BKN", "Nets": "BKN", "BKN": "BKN", "BK": "BKN",
    "Charlotte Hornets": "CHA", "Hornets": "CHA", "CHA": "CHA", "CHO": "CHA",
    "Chicago Bulls": "CHI", "Bulls": "CHI", "CHI": "CHI",
    "Cleveland Cavaliers": "CLE", "Cavaliers": "CLE", "Cavs": "CLE", "CLE": "CLE",
    "Dallas Mavericks": "DAL", "Mavericks": "DAL", "Mavs": "DAL", "DAL": "DAL",
    "Denver Nuggets": "DEN", "Nuggets": "DEN", "DEN": "DEN",
    "Detroit Pistons": "DET", "Pistons": "DET", "DET": "DET",
    "Golden State Warriors": "GSW", "Warriors": "GSW", "GSW": "GSW", "GS": "GSW",
    "Houston Rockets": "HOU", "Rockets": "HOU", "HOU": "HOU",
    "Indiana Pacers": "IND", "Pacers": "IND", "IND": "IND",
    "LA Clippers": "LAC", "Clippers": "LAC", "LAC": "LAC",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL", "Lakers": "LAL", "LAL": "LAL",
    "Memphis Grizzlies": "MEM", "Grizzlies": "MEM", "MEM": "MEM",
    "Miami Heat": "MIA", "Heat": "MIA", "MIA": "MIA",
    "Milwaukee Bucks": "MIL", "Bucks": "MIL", "MIL": "MIL",
    "Minnesota Timberwolves": "MIN", "Timberwolves": "MIN", "Wolves": "MIN", "MIN": "MIN",
    "New Orleans Pelicans": "NOP", "Pelicans": "NOP", "NOP": "NOP", "NO": "NOP",
    "New York Knicks": "NYK", "Knicks": "NYK", "NYK": "NYK", "NY": "NYK",
    "Oklahoma City Thunder": "OKC", "Thunder": "OKC", "OKC": "OKC",
    "Orlando Magic": "ORL", "Magic": "ORL", "ORL": "ORL",
    "Philadelphia 76ers": "PHI", "76ers": "PHI", "Sixers": "PHI", "PHI": "PHI",
    "Phoenix Suns": "PHX", "Suns": "PHX", "PHX": "PHX", "PHO": "PHX",
    "Portland Trail Blazers": "POR", "Trail Blazers": "POR", "Blazers": "POR", "POR": "POR",
    "Sacramento Kings": "SAC", "Kings": "SAC", "SAC": "SAC",
    "San Antonio Spurs": "SAS", "Spurs": "SAS", "SAS": "SAS", "SA": "SAS",
    "Toronto Raptors": "TOR", "Raptors": "TOR", "TOR": "TOR",
    "Utah Jazz": "UTA", "Jazz": "UTA", "UTA": "UTA", "UTAH": "UTA",
    "Washington Wizards": "WAS", "Wizards": "WAS", "WAS": "WAS", "WSH": "WAS",
}

ALL_TEAM_ABBRS = sorted(set(TEAM_ABBR_MAP.values()))

# Primary brand color per team, used to tint the shot-zone court with the
# player's own team rather than a generic accent color.
TEAM_COLORS = {
    "ATL": "#E03A3E", "BKN": "#000000", "BOS": "#007A33", "CHA": "#1D1160",
    "CHI": "#CE1141", "CLE": "#860038", "DAL": "#00538C", "DEN": "#0E2240",
    "DET": "#C8102E", "GSW": "#1D428A", "HOU": "#CE1141", "IND": "#002D62",
    "LAC": "#C8102E", "LAL": "#552583", "MEM": "#5D76A9", "MIA": "#98002E",
    "MIL": "#00471B", "MIN": "#0C2340", "NOP": "#0C2340", "NYK": "#006BB6",
    "OKC": "#007AC1", "ORL": "#0077C0", "PHI": "#006BB6", "PHX": "#1D1160",
    "POR": "#E03A3E", "SAC": "#5A2D81", "SAS": "#C4CED4", "TOR": "#CE1141",
    "UTA": "#002B5C", "WAS": "#002B5C",
}


def normalize_team(name: str):
    name = name.strip()
    if name.upper() in TEAM_ABBR_MAP:
        return TEAM_ABBR_MAP[name.upper()]
    if name in TEAM_ABBR_MAP:
        return TEAM_ABBR_MAP[name]
    for key, abbr in TEAM_ABBR_MAP.items():
        if name.lower() in key.lower() or key.lower() in name.lower():
            return abbr
    return None
