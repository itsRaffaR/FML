"""Pick an FPL squad from this season's gameweek-by-gameweek data.

Scoring is built from GW1-3 underlying numbers (xG, xA, expected goals
conceded, defensive contributions) rather than last season's totals, because
a summer move makes last season's rate a measure of a job the player no
longer holds. Three gameweeks of actual points are far too noisy to use
alone, so the expected-points model carries most of the weight and realised
points-per-90 contributes the rest (it captures bonus-earning that xP misses).
"""
import csv, math, argparse
import pulp

SQUAD = {'GK': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
XI_MIN = {'GK': 1, 'DEF': 3, 'MID': 2, 'FWD': 1}
XI_MAX = {'GK': 1, 'DEF': 5, 'MID': 5, 'FWD': 3}
GOAL_PTS = {'GK': 10, 'DEF': 6, 'MID': 5, 'FWD': 4}
CS_PTS = {'GK': 4, 'DEF': 4, 'MID': 1, 'FWD': 0}
DEFCON_NEED = {'DEF': 10, 'MID': 12, 'FWD': 12, 'GK': 99}


def num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def load(p):
    with open(p) as fh:
        return list(csv.DictReader(fh))


def aggregate(gw_files, positions):
    """Sum each player's GW1-3 returns into one row per player.

    A snapshot taken while a match is still being played records partial
    minutes for both clubs in it. Per-90 rates are unaffected, but the share
    of available minutes is not, so track how much football each club has
    actually had recorded and divide by that rather than by 90 a gameweek.
    """
    agg = {}
    available = {}
    for path in gw_files:
        rows = load(path)
        played = {}
        for r in rows:                       # longest appearance = match length
            club = positions.get(r['id'], ('?', '?'))[1]
            played[club] = max(played.get(club, 0.0), num(r['minutes']))
        for club, mx in played.items():
            available[club] = available.get(club, 0.0) + mx
        for r in rows:
            pid = r['id']
            a = agg.setdefault(pid, {
                'name': r['web_name'], 'cost': num(r['now_cost']), 'status': r['status'],
                'mins': 0.0, 'pts': 0.0, 'xg': 0.0, 'xa': 0.0, 'xgc': 0.0,
                'defcon': 0.0, 'saves': 0.0, 'bonus': 0.0, 'starts': 0.0, 'last_mins': 0.0,
                'last_available': 90.0,
            })
            a['cost'] = num(r['now_cost'], a['cost'])   # latest gameweek's price
            a['status'] = r['status']
            a['mins'] += num(r['minutes'])
            a['pts'] += num(r['total_points'])
            a['xg'] += num(r['expected_goals'])
            a['xa'] += num(r['expected_assists'])
            a['xgc'] += num(r['expected_goals_conceded'])
            a['defcon'] += num(r['defensive_contribution'])
            a['saves'] += num(r['saves'])
            a['bonus'] += num(r['bonus'])
            a['starts'] += num(r['starts'])
            a['last_mins'] = num(r['minutes'])          # most recent gameweek
            a['last_available'] = played.get(positions.get(pid, ('?', '?'))[1], 90.0) or 90.0
    for pid, a in agg.items():
        a['pos'] = positions.get(pid, ('?', '?'))[0]
        a['club'] = positions.get(pid, ('?', '?'))[1]
        a['available'] = available.get(a['club'], 270.0) or 270.0
    return agg, available


def expected_points_per_90(a):
    """Points a player would be expected to score per 90, from underlying."""
    pos, m = a['pos'], a['mins']
    if m <= 0:
        return 0.0
    per90 = lambda v: v / m * 90
    xg90, xa90, xgc90 = per90(a['xg']), per90(a['xa']), per90(a['xgc'])

    xp = 2.0                                     # playing 60+ minutes
    xp += xg90 * GOAL_PTS.get(pos, 4)
    xp += xa90 * 3
    xp += math.exp(-xgc90) * CS_PTS.get(pos, 0)  # Poisson P(concede nothing)
    if pos == 'GK':
        xp += per90(a['saves']) / 3
        xp -= xgc90 / 2                          # a goal conceded costs a keeper
    elif pos == 'DEF':
        xp -= xgc90 / 2

    need = DEFCON_NEED.get(pos, 99)              # defensive-contribution bonus
    xp += min(per90(a['defcon']) / need, 1.0) ** 2 * 2
    return xp


def build(agg, ease, horizon, exclude, min_last_mins, blend):
    out = []
    for pid, a in agg.items():
        if a['name'] in exclude or a['status'] != 'a':
            continue
        if a['mins'] < 90 or a['last_mins'] < min_last_mins * (a['last_available'] / 90.0):
            continue                             # not playing now
        xp90 = expected_points_per_90(a)
        actual90 = a['pts'] / a['mins'] * 90
        rate = blend * xp90 + (1 - blend) * actual90
        share = min(a['mins'] / a['available'], 1.0)   # of minutes actually recorded
        mult = ease.get(a['club'], (3.0, 1.0))[1]
        out.append({**a, 'xp90': xp90, 'actual90': actual90,
                    'proj': rate * share * horizon * mult})
    return out


def fixture_ease(fixtures, teams, start, end):
    d = {}
    for f in fixtures:
        if not f['event'].strip():
            continue
        gw = int(f['event'])
        if not start <= gw <= end:
            continue
        d.setdefault(teams[f['team_h']], []).append(int(f['team_h_difficulty']))
        d.setdefault(teams[f['team_a']], []).append(int(f['team_a_difficulty']))
    return {t: (sum(v) / len(v), 1 + (3 - sum(v) / len(v)) * 0.12) for t, v in d.items()}


def solve(P, budget, clashes, force, bench_weight=0.15):
    prob = pulp.LpProblem('fpl', pulp.LpMaximize)
    idx = range(len(P))
    sq = pulp.LpVariable.dicts('s', idx, cat='Binary')
    xi = pulp.LpVariable.dicts('x', idx, cat='Binary')
    cap = pulp.LpVariable.dicts('c', idx, cat='Binary')

    prob += pulp.lpSum(P[i]['proj'] * (xi[i] + cap[i] + bench_weight * (sq[i] - xi[i])) for i in idx)
    prob += pulp.lpSum(P[i]['cost'] * sq[i] for i in idx) <= budget
    prob += pulp.lpSum(xi[i] for i in idx) == 11
    prob += pulp.lpSum(cap[i] for i in idx) == 1
    # nobody captains a defender: the armband goes on an attacking asset
    for i in idx:
        if P[i]['pos'] in ('GK', 'DEF'):
            prob += cap[i] == 0
    for i in idx:
        prob += xi[i] <= sq[i]
        prob += cap[i] <= xi[i]
    for pos, n in SQUAD.items():
        prob += pulp.lpSum(sq[i] for i in idx if P[i]['pos'] == pos) == n
        prob += pulp.lpSum(xi[i] for i in idx if P[i]['pos'] == pos) >= XI_MIN[pos]
        prob += pulp.lpSum(xi[i] for i in idx if P[i]['pos'] == pos) <= XI_MAX[pos]
    for club in {p['club'] for p in P}:
        prob += pulp.lpSum(sq[i] for i in idx if P[i]['club'] == club) <= 3
    for n, (h, a) in enumerate(clashes):          # no clean sheet at both ends
        side = pulp.LpVariable(f'z{n}', cat='Binary')
        bk = lambda c: [i for i in idx if P[i]['club'] == c and P[i]['pos'] in ('GK', 'DEF')]
        prob += pulp.lpSum(sq[i] for i in bk(h)) <= 5 * side
        prob += pulp.lpSum(sq[i] for i in bk(a)) <= 5 * (1 - side)
    for name in force:
        want = [i for i in idx if P[i]['name'] == name]
        if not want:
            raise SystemExit(f"cannot force {name!r}: not eligible")
        prob += pulp.lpSum(sq[i] for i in want) == 1

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    picked = [(P[i], xi[i].value() > 0.5) for i in idx if sq[i].value() > 0.5]
    return pulp.LpStatus[prob.status], picked, next(P[i] for i in idx if cap[i].value() > 0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--budget', type=float, default=99.5)
    ap.add_argument('--from-gw', type=int, default=4)
    ap.add_argument('--to-gw', type=int, default=10)
    ap.add_argument('--exclude', default='')
    ap.add_argument('--force', default='')
    ap.add_argument('--min-last-mins', type=float, default=45,
                    help='minutes in the most recent gameweek to count as playing')
    ap.add_argument('--blend', type=float, default=0.65,
                    help='weight on the underlying model vs realised points')
    ap.add_argument('--ins', default='ins')
    ap.add_argument('--fpl', default='.')
    args = ap.parse_args()

    players = load(f'{args.ins}/players.csv')
    # teams are keyed by `code` here, which is what players.csv references
    teams_ins = {r['code']: r['short_name'] for r in load(f'{args.ins}/teams.csv')}
    POSN = {'Goalkeeper': 'GK', 'Defender': 'DEF', 'Midfielder': 'MID', 'Forward': 'FWD'}
    positions = {r['player_id']: (POSN.get(r['position'], '?'),
                                  teams_ins.get(r['team_code'], '?')) for r in players}

    agg, available = aggregate([f'{args.ins}/pgw{g}.csv' for g in (1, 2, 3)], positions)

    fteams = {r['id']: r['short_name'] for r in load(f'{args.fpl}/teams.csv')}
    fx = load(f'{args.fpl}/fixtures.csv')
    ease = fixture_ease(fx, fteams, args.from_gw, args.to_gw)
    clashes = [(fteams[f['team_h']], fteams[f['team_a']]) for f in fx
               if f['event'].strip() and int(f['event']) == args.from_gw]

    P = build(agg, ease, args.to_gw - args.from_gw + 1,
              {x.strip() for x in args.exclude.split(',') if x.strip()},
              args.min_last_mins, args.blend)
    status, picked, captain = solve(P, args.budget, clashes,
                                    [x.strip() for x in args.force.split(',') if x.strip()])

    print(f"solver: {status}   pool: {len(P)}   scoring: GW1-3 underlying "
          f"(blend {args.blend:.2f} xP / {1-args.blend:.2f} actual)")
    order = {'GK': 0, 'DEF': 1, 'MID': 2, 'FWD': 3}
    for label, want in (('STARTING XI', True), ('BENCH', False)):
        print(f"\n{label}")
        for p in sorted([q for q, s in picked if s == want], key=lambda p: (order[p['pos']], -p['proj'])):
            print(f"  {p['pos']:<4}{p['name']:<15}{p['club']:<5}£{p['cost']:>5.1f}  proj {p['proj']:>5.1f}"
                  f"  xP/90 {p['xp90']:>4.1f}  actual/90 {p['actual90']:>4.1f}"
                  f"  mins {p['mins']:>5.0f}  pts {p['pts']:>4.0f}")
    print(f"\ntotal £{sum(p['cost'] for p, _ in picked):.1f}m   XI projection "
          f"{sum(p['proj'] for p, s in picked if s):.1f}   captain: {captain['name']}")


if __name__ == '__main__':
    main()
