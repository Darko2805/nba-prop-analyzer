import argparse
import sys

from .analysis.prop_analyzer import PropAnalyzer
from .output.formatter import format_prediction
from .config import PROP_TYPES
from .data.team_mapping import ALL_TEAM_ABBRS


def main():
    parser = argparse.ArgumentParser(
        description="NBA Player Prop Analyzer - Matchup-based probability estimates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m nba_prop_analyzer "Luka Doncic" OKC points 28.5
  python -m nba_prop_analyzer "Nikola Jokic" LAL pra 52.5 --trend 1.03
  python -m nba_prop_analyzer "Stephen Curry" BOS 3pm 4.5
  python -m nba_prop_analyzer "Jayson Tatum" MIL rebounds 8.5 --trend 0.95

Prop Types:
  points    - Total points scored
  rebounds  - Total rebounds
  assists   - Total assists
  3pm       - Three-pointers made
  pra       - Points + Rebounds + Assists combined

Trend Override:
  --trend 1.05  = player is on a +5% hot streak
  --trend 0.95  = player is on a -5% cold streak
        """,
    )
    parser.add_argument("player", type=str, help="Player name (e.g., 'Luka Doncic')")
    parser.add_argument("opponent", type=str, help=f"Opponent team abbreviation (e.g., OKC)")
    parser.add_argument("prop_type", choices=PROP_TYPES, help="Type of prop bet")
    parser.add_argument("line", type=float, help="Prop line (e.g., 28.5)")
    parser.add_argument(
        "--trend", type=float, default=1.0,
        help="Manual trend override factor (default: 1.0 = neutral)"
    )

    args = parser.parse_args()

    print("\n  NBA PROP ANALYZER")
    print("  Loading data from databallr + TeamRankings + basketball-reference...\n")

    try:
        analyzer = PropAnalyzer()
        analyzer.load_data()

        prediction = analyzer.analyze_prop(
            player_name=args.player,
            opponent_abbr=args.opponent.upper(),
            prop_type=args.prop_type,
            prop_line=args.line,
            trend_override=args.trend,
        )

        print(format_prediction(prediction))

    except ValueError as e:
        print(f"\n  Error: {e}\n", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n  Unexpected error: {e}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
