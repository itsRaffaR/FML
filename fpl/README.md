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

Last season's minutes were earned at whatever club the player was at then, so
on their own they say nothing about whether he starts now. The pool therefore
also requires `--min-minutes-now` minutes in the CURRENT season (default 60):
without it the solver happily picks a backup keeper who has not played a league
minute for his new club.

Known limits: the prior is last season only, so players whose minutes were
injury-hit are under-rated, and anyone who arrived in the league this summer is
missing entirely (the pool requires 450+ minutes last season). The current-season
minutes check is only as deep as the mirror goes — one gameweek — so it neither
catches a player dropped later in the season nor spares one who was rested in
GW1. Check the XI against the latest confirmed team sheets before submitting.

## Usage

    python3 optimise_squad.py --budget 100.0 --from-gw 4 --to-gw 10 \
        --exclude "Saliba,Simons" --force "Haaland" --data /path/to/csvs

`--force` requires named players in the squad, so you can price the cost of a
pick the model would not make on its own.

## Injuries

The snapshot's `status` / `news` fields are only as fresh as the mirror, which
currently stops after GW1 — three gameweeks and a transfer deadline day behind.
`exclusions.txt` records the hand-checked absences and wrong-club entries that
must be passed to `--exclude`; re-verify it against team news before every
deadline.
