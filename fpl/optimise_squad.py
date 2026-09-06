"""Pick an FPL squad by integer programming.

Prices, clubs and positions come from the live-game snapshot; the scoring prior
comes from last season's per-90 rate, scaled by each club's fixture difficulty
over the planning horizon.
"""
import csv, argparse
import pulp

POS = {'1': 'GK', '2': 'DEF', '3': 'MID', '4': 'FWD'}
SQUAD = {'GK': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
XI_MIN = {'GK': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GK': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}


def load(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def fixture_ease(fixtures, start, end):
    """Mean difficulty per team over the horizon, mapped to a scoring multiplier."""
    diff = {}
    for f in fixtures:
        if not f['event'].strip():
            continue
        gw = int(f['event'])
        if not start <= gw <= end:
            continue
        diff.setdefault(f['team_h'], []).append(int(f['team_h_difficulty']))
        diff.setdefault(f['team_a'], []).append(int(f['team_a_difficulty']))
    # difficulty runs 1 (easiest) to 5; 3 is neutral
    return {t: (sum(d) / len(d), 1 + (3 - sum(d) / len(d)) * 0.12) for t, d in diff.items()}


def build_players(cur, prev, ease, teams, horizon, exclude, min_minutes_now=60):
    prior = {r['code']: r for r in prev}
    out = []
    for r in cur:
        pos, club = POS[r['element_type']], teams[r['team']]
        if r['web_name'] in exclude or r['status'] != 'a':
            continue
        # Last season's minutes were earned at whatever club the player was at
        # then, so they say nothing about whether he starts NOW. Require
        # evidence of minutes at the current club before trusting the rate.
        if int(r['minutes']) < min_minutes_now:
            continue

        p = prior.get(r['code'])
        mins25 = int(p['minutes']) if p else 0
        pts25 = int(p['total_points']) if p else 0
        if mins25 < 450:            # too small a sample to project from
            continue
        pp90 = pts25 / mins25 * 90
        starts = min(mins25 / (38 * 90), 1.0)          # nailedness proxy
        avg_diff, mult = ease[r['team']]
        out.append({
            'name': r['web_name'], 'club': club, 'pos': pos,
            'cost': int(r['now_cost']) / 10,
            'proj': pp90 * starts * horizon * mult,
            'pp90': pp90, 'starts': starts, 'diff': avg_diff, 'pts25': pts25,
        })
    return out


def solve(players, budget, bench_weight=0.15, force=(), clashes=()):
    prob = pulp.LpProblem('fpl', pulp.LpMaximize)
    idx = range(len(players))
    sq = pulp.LpVariable.dicts('squad', idx, cat='Binary')
    xi = pulp.LpVariable.dicts('xi', idx, cat='Binary')
    cap = pulp.LpVariable.dicts('cap', idx, cat='Binary')

    # the armband doubles one starter every week, so it is part of the objective
    prob += pulp.lpSum(players[i]['proj'] * (xi[i] + cap[i] + bench_weight * (sq[i] - xi[i]))
                       for i in idx)
    prob += pulp.lpSum(cap[i] for i in idx) == 1
    for i in idx:
        prob += cap[i] <= xi[i]
    prob += pulp.lpSum(players[i]['cost'] * sq[i] for i in idx) <= budget
    prob += pulp.lpSum(xi[i] for i in idx) == 11
    for i in idx:
        prob += xi[i] <= sq[i]
    for pos, n in SQUAD.items():
        prob += pulp.lpSum(sq[i] for i in idx if players[i]['pos'] == pos) == n
        prob += pulp.lpSum(xi[i] for i in idx if players[i]['pos'] == pos) >= XI_MIN[pos]
        prob += pulp.lpSum(xi[i] for i in idx if players[i]['pos'] == pos) <= XI_MAX[pos]
    for club in {p['club'] for p in players}:
        prob += pulp.lpSum(sq[i] for i in idx if players[i]['club'] == club) <= 3

    # a clean sheet cannot happen at both ends of the same match, so never hold
    # keepers or defenders on both sides of one fixture
    for n, (home, away) in enumerate(clashes):
        side = pulp.LpVariable(f'side_{n}', cat='Binary')
        back = lambda club: [i for i in idx
                             if players[i]['club'] == club and players[i]['pos'] in ('GK', 'DEF')]
        prob += pulp.lpSum(sq[i] for i in back(home)) <= 5 * side
        prob += pulp.lpSum(sq[i] for i in back(away)) <= 5 * (1 - side)

    for name in force:
        want = [i for i in idx if players[i]['name'] == name]
        if not want:
            raise SystemExit(f"cannot force {name!r}: not in the eligible pool")
        prob += pulp.lpSum(sq[i] for i in want) == 1

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    status = pulp.LpStatus[prob.status]
    picked = [(players[i], xi[i].value() > 0.5) for i in idx if sq[i].value() > 0.5]
    captain = next(players[i] for i in idx if cap[i].value() > 0.5)
    return status, picked, captain


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--budget', type=float, default=100.0)
    ap.add_argument('--from-gw', type=int, default=4)
    ap.add_argument('--to-gw', type=int, default=10)
    ap.add_argument('--exclude', default='')
    ap.add_argument('--force', default='', help='comma-separated web_names to require in the squad')
    ap.add_argument('--data', default='.')
    ap.add_argument('--min-minutes-now', type=int, default=60,
                    help='minutes required in the current season to count as a starter')
    args = ap.parse_args()

    d = args.data.rstrip('/')
    teams = {r['id']: r['short_name'] for r in load(f'{d}/teams.csv')}
    ease = fixture_ease(load(f'{d}/fixtures.csv'), args.from_gw, args.to_gw)
    exclude = {x.strip() for x in args.exclude.split(',') if x.strip()}
    horizon = args.to_gw - args.from_gw + 1

    fx = load(f'{d}/fixtures.csv')
    clashes = [(teams[f['team_h']], teams[f['team_a']]) for f in fx
               if f['event'].strip() and int(f['event']) == args.from_gw]

    players = build_players(load(f'{d}/players_raw_2026-27.csv'),
                            load(f'{d}/players_raw_2025-26.csv'),
                            ease, teams, horizon, exclude, args.min_minutes_now)
    force = [x.strip() for x in args.force.split(',') if x.strip()]
    status, picked, captain = solve(players, args.budget, force=force, clashes=clashes)

    print(f"solver: {status}   pool: {len(players)}   horizon: GW{args.from_gw}-{args.to_gw}")
    order = {'GK': 0, 'DEF': 1, 'MID': 2, 'FWD': 3}
    for label, want in (('STARTING XI', True), ('BENCH', False)):
        print(f"\n{label}")
        rows = sorted([p for p, s in picked if s == want], key=lambda p: (order[p['pos']], -p['proj']))
        for p in rows:
            print(f"  {p['pos']:<4}{p['name']:<16}{p['club']:<5}£{p['cost']:>5.1f}"
                  f"  proj {p['proj']:>5.1f}  pp90 {p['pp90']:.2f}  fdr {p['diff']:.2f}  25/26 {p['pts25']:>3}")
    print(f"\ntotal cost £{sum(p['cost'] for p, _ in picked):.1f}m"
          f"   XI projection {sum(p['proj'] for p, s in picked if s):.1f} pts")
    print(f"captain: {captain['name']} ({captain['club']})")


if __name__ == '__main__':
    main()
