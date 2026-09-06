# FPL squad optimiser

Picks a squad by integer programming: maximise projected points subject to the
FPL rules (£100.0m budget, 2/5/5/3 squad, max 3 players per club, a legal XI).

## Data

Pulled from the public mirror of the FPL API at
`vaastav/Fantasy-Premier-League` (the game's own API is unreachable from this
environment):

    data/2026-27/players_raw.csv   prices, clubs, positions
    data/2025-26/players_raw.csv   scoring prior (full 38-gameweek season)
    data/2026-27/fixtures.csv      per-gameweek fixture difficulty
    data/2026-27/teams.csv         club id -> short name

Players are linked across seasons on `code`, which is stable; `team` ids are
re-indexed each season and are NOT comparable between files.

## Scoring

    proj = (last season points per 90) x (share of minutes played) x gameweeks
           x fixture multiplier

where the multiplier is `1 + (3 - mean difficulty) * 0.12` over the horizon.

Known limits: the prior is last season only, so players whose minutes were
injury-hit are under-rated, and anyone who arrived in the league this summer is
missing entirely (the pool requires 450+ minutes last season).

## Usage

    python3 optimise_squad.py --budget 100.0 --from-gw 4 --to-gw 10 \
        --exclude "Saliba,Simons" --force "Haaland" --data /path/to/csvs

`--force` requires named players in the squad, so you can price the cost of a
pick the model would not make on its own.
