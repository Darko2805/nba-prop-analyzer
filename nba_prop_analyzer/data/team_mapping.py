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
