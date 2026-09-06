# Squad optimiser on this season's data

`optimise_gw13.py` replaces the last-season prior in `optimise_squad.py`. A
summer transfer makes last season's rate a measure of a job the player no
longer holds, so scoring is built from GW1-3 of the current campaign.

## Data

`olbauday/FPL-Core-Insights`, which publishes discrete per-gameweek stats:

    data/2026-2027/By Gameweek/GW{n}/player_gameweek_stats.csv
    data/2026-2027/players.csv    player_id -> position, team_code
    data/2026-2027/teams.csv      code -> short_name

Fixture difficulty still comes from the FPL fixtures file.

Verified against independently known results before use: Haaland 3 goals in
GW1-3, Isak's GW3 brace, Rice's GW3 assist, Senesi 90/0/0 and Dubravka 0/0/0.

## Scoring

Expected points per 90 from underlying numbers:

    2                                  playing the match
    + xG/90  x goal points by position
    + xA/90  x 3
    + exp(-xGC/90) x clean sheet points     Poisson P(concede nothing)
    - xGC/90 / 2                            for keepers and defenders
    + saves/90 / 3                          keepers
    + min(DefCon90 / threshold, 1)^2 x 2    threshold 10 for DEF, 12 for MID
    + bonus/90

Bonus is included because the BPS system rewards the same actions the rest of
the model already counts, so the players who earn bonus keep earning it.
Leaving it out quietly penalises high-BPS attackers: it was docking Haaland two
points per 90 against cheap defenders who earn almost none, and it alone
accounted for most of the apparent case against owning him.

Blended with realised points per 90 (default 0.85 / 0.15). Three gameweeks of
points are far too noisy to use alone, but realised points capture bonus
earning that the model misses. A lower blend chases hot starts: at 0.65 the
solver fills the XI with Hull's defence, who have the second-worst fixtures
and the largest overperformance in the league.

## Partial snapshots

A snapshot taken while a match is in progress records partial minutes for both
clubs. In GW3 every Arsenal and Chelsea player shows exactly 45 minutes.
Per-90 rates are unaffected, but the share-of-minutes term is, so the script
measures each club's recorded match length per gameweek and divides by that.
