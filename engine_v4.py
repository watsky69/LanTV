"""
LanUs Rating 3.0 (v4.0 Final) - Universal CS2 Demo Analysis Engine
HLTV Rating 3.0 Full Analog with Strict Eco-Adjustment, Round Swing & Clutches (1v1, 1v2, 1v3, 1v4, 1v5)
Works dynamically on ANY CS2 demo: MR12, MR15, Overtime, Faceit, Cybershoke, Valve MM.
"""

import sys
import os
import math
from collections import defaultdict
import numpy as np
import pandas as pd
from demoparser2 import DemoParser

# Win Probability Lookup Matrix for CS2 (Team Alive, Opponent Alive) -> P(Win)
WP_TABLE = {
    (5, 5): 0.500, (5, 4): 0.725, (5, 3): 0.865, (5, 2): 0.952, (5, 1): 0.991, (5, 0): 1.000,
    (4, 5): 0.275, (4, 4): 0.500, (4, 3): 0.732, (4, 2): 0.884, (4, 1): 0.970, (4, 0): 1.000,
    (3, 5): 0.135, (3, 4): 0.268, (3, 3): 0.500, (3, 2): 0.745, (3, 1): 0.925, (3, 0): 1.000,
    (2, 5): 0.048, (2, 4): 0.116, (2, 3): 0.255, (2, 2): 0.500, (2, 1): 0.765, (2, 0): 1.000,
    (1, 5): 0.009, (1, 4): 0.030, (1, 3): 0.075, (1, 2): 0.235, (1, 1): 0.500, (1, 0): 1.000,
    (0, 5): 0.000, (0, 4): 0.000, (0, 3): 0.000, (0, 2): 0.000, (0, 1): 0.000, (0, 0): 0.500
}

def get_wp(team_alive: int, opp_alive: int) -> float:
    t_a = max(0, min(5, int(team_alive)))
    o_a = max(0, min(5, int(opp_alive)))
    return WP_TABLE.get((t_a, o_a), 0.500)


class LanUsEngineV4:
    def __init__(self, demo_path: str):
        if not os.path.exists(demo_path):
            raise FileNotFoundError(f"Demo file not found: {demo_path}")
        self.demo_path = demo_path
        self.parser = DemoParser(demo_path)

    def analyze(self) -> dict:
        parser = self.parser

        # 1. Header info
        try:
            header = parser.parse_header()
            map_name = header.get('map_name', 'Unknown')
            server_name = header.get('server_name', 'CS2 Server')
        except Exception:
            map_name = 'CS2 Map'
            server_name = 'CS2 Server'

        # 2. Extract events
        r_ends = parser.parse_event('round_end', other=['is_warmup_period', 'is_match_started'])
        r_starts = parser.parse_event('round_start', other=['is_warmup_period', 'is_match_started'])
        deaths_raw = parser.parse_event('player_death',
                                        player=['armor_value', 'has_helmet', 'current_equip_value', 'team_name', 'team_num'])
        hurt_raw = parser.parse_event('player_hurt',
                                      player=['armor_value', 'has_helmet', 'current_equip_value', 'team_name', 'team_num'])

        # 3. Dynamic Filtering: Find official match start and discard knife/warmup rounds
        match_starts = parser.parse_event('round_announce_match_start')
        min_match_tick = 0
        if len(match_starts) > 0:
            min_match_tick = match_starts.iloc[-1]['tick']

        official_rounds = []
        for idx, r in r_ends.iterrows():
            end_tick = r['tick']
            prev_starts = r_starts[r_starts['tick'] < end_tick]
            start_tick = prev_starts.iloc[-1]['tick'] if len(prev_starts) > 0 else (end_tick - 2000)

            r_deaths = deaths_raw[deaths_raw['tick'].between(start_tick, end_tick)]

            # Check if 100% knife kills in round before match start
            if len(r_deaths) > 0:
                weapons = r_deaths['weapon'].tolist()
                is_all_knife = all('knife' in str(w).lower() or 'bayonet' in str(w).lower() for w in weapons)
                if is_all_knife and end_tick <= min_match_tick:
                    continue

            # Warmup flag or before final match start announcement
            if r.get('is_warmup_period') == True or end_tick < min_match_tick:
                continue

            official_rounds.append({
                'round_num': len(official_rounds) + 1,
                'start_tick': start_tick,
                'end_tick': end_tick,
                'winner': str(r.get('winner', 'Unknown')),
                'reason': str(r.get('reason', 'Unknown'))
            })

        rounds_df = pd.DataFrame(official_rounds)
        total_rounds = len(rounds_df)
        if total_rounds == 0:
            raise ValueError("No official match rounds detected after applying warmup and knife round filters.")

        filter_message = f"🧹 Фильтр демки: Удалены данные разминки / ножевого раунда. В расчет вошли только {total_rounds} официальных раундов."

        # Filter deaths and hurt within official match bounds
        min_tick = rounds_df['start_tick'].min()
        max_tick = rounds_df['end_tick'].max() + 128

        deaths = deaths_raw[deaths_raw['tick'].between(min_tick, max_tick)].copy()
        hurt = hurt_raw[hurt_raw['tick'].between(min_tick, max_tick)].copy()

        def get_round_idx(tick):
            for _, r in rounds_df.iterrows():
                if r['start_tick'] <= tick <= r['end_tick'] + 128:
                    return int(r['round_num'])
            return None

        deaths['round_num'] = deaths['tick'].apply(get_round_idx)
        deaths = deaths.dropna(subset=['round_num']).copy()
        deaths['round_num'] = deaths['round_num'].astype(int)

        hurt['round_num'] = hurt['tick'].apply(get_round_idx)
        hurt = hurt.dropna(subset=['round_num']).copy()
        hurt['round_num'] = hurt['round_num'].astype(int)

        # Players info & SteamID64 mapping
        players_info = parser.parse_player_info()
        all_players = sorted(list(players_info['name'].unique()))
        player_to_steamid = {}
        for _, pi_row in players_info.iterrows():
            p_name = str(pi_row.get('name', ''))
            s_id = pi_row.get('steamid')
            if p_name and pd.notnull(s_id):
                player_to_steamid[p_name] = str(int(s_id)) if str(s_id).isdigit() else str(s_id)

        # 4. Universal Team Tracking:
        # Determine rosters of Team 1 (starts CT in R1) and Team 2 (starts T in R1)
        r1 = rounds_df.iloc[0]
        r1_deaths = deaths[deaths['round_num'] == 1]
        team1_players = set()
        team2_players = set()

        for p in all_players:
            p_d = r1_deaths[(r1_deaths['attacker_name'] == p) | (r1_deaths['user_name'] == p)]
            if len(p_d) > 0:
                side = p_d.iloc[0]['attacker_team_name'] if p_d.iloc[0]['attacker_name'] == p else p_d.iloc[0]['user_team_name']
                if 'CT' in str(side):
                    team1_players.add(p)
                else:
                    team2_players.add(p)
            else:
                # Early check across first 6 rounds
                p_early = deaths[(deaths['round_num'] <= 6) & ((deaths['attacker_name'] == p) | (deaths['user_name'] == p))]
                if len(p_early) > 0:
                    side = p_early.iloc[0]['attacker_team_name'] if p_early.iloc[0]['attacker_name'] == p else p_early.iloc[0]['user_team_name']
                    if 'CT' in str(side):
                        team1_players.add(p)
                    else:
                        team2_players.add(p)
                else:
                    team1_players.add(p)

        # Team names based on top / captain players
        team1_name = "Team 1"
        team2_name = "Team 2"
        # Find captain if present (e.g. torchhy, kokoska1915)
        for p in team1_players:
            if 'torchhy' in p.lower():
                team1_name = "team_torchhy"
                break
            if 'slewor' in p.lower() and team1_name == "Team 1":
                team1_name = "team_slewor"
        for p in team2_players:
            if 'kokoska' in p.lower():
                team2_name = "team_kokoska"
                break
            if 'watsky' in p.lower() and team2_name == "Team 2":
                team2_name = "team_watsky"

        # Track side for every player in every round
        def get_player_side_in_round(p, r_num):
            # Check deaths in that round
            d_rnd = deaths[deaths['round_num'] == r_num]
            p_d = d_rnd[(d_rnd['attacker_name'] == p) | (d_rnd['user_name'] == p)]
            if len(p_d) > 0:
                side = p_d.iloc[0]['attacker_team_name'] if p_d.iloc[0]['attacker_name'] == p else p_d.iloc[0]['user_team_name']
                return 'CT' if 'CT' in str(side) else 'T'
            # Look at teammates in that round
            is_team1 = (p in team1_players)
            for _, row in d_rnd.iterrows():
                if (row['attacker_name'] in team1_players) == is_team1:
                    return 'CT' if 'CT' in str(row['attacker_team_name']) else 'T'
                if (row['user_name'] in team1_players) == is_team1:
                    return 'CT' if 'CT' in str(row['user_team_name']) else 'T'
            # Fallback by regulation/OT halves
            if r_num <= 12:
                return 'CT' if is_team1 else 'T'
            elif r_num <= 24:
                return 'T' if is_team1 else 'CT'
            elif r_num <= 27: # OT 1 half 1
                return 'T' if is_team1 else 'CT'
            else: # OT 1 half 2
                return 'CT' if is_team1 else 'T'

        # 5. Initialize Stats
        stats = defaultdict(lambda: {
            'kills': 0, 'deaths': 0, 'assists': 0, 'headshots': 0,
            'damage': 0.0, 'eco_kills': 0.0, 'eco_damage': 0.0, 'round_swing': 0.0,
            'opening_kills': 0, 'opening_deaths': 0,
            'pressure_kills': 0, 'anti_eco_kills': 0,
            'multi_2k': 0, 'multi_3k': 0, 'multi_4k': 0, 'multi_5k': 0,
            # Clutches
            'clutch_1v1': 0, 'clutch_1v2': 0, 'clutch_1v3': 0, 'clutch_1v4': 0, 'clutch_1v5': 0,
            # Real Sniper & Utility & Trade Kills Telemetry
            'awp_kills': 0, 'sniper_kills': 0,
            'rounds_with_awp': set(), 'rounds_with_sniper': set(),
            'trade_kills': 0,
            'smokes_thrown': 0, 'flashes_thrown': 0, 'molotovs_thrown': 0, 'he_thrown': 0,
            'enemies_flashed': 0, 'utility_damage': 0.0,
            'rounds_with_kill': set(), 'rounds_with_assist': set(),
            'rounds_survived': set(), 'rounds_traded_death': set(),
            't_kills': 0, 't_deaths': 0, 't_damage': 0.0, 't_eco_kills': 0.0, 't_round_swing': 0.0,
            'ct_kills': 0, 'ct_deaths': 0, 'ct_damage': 0.0, 'ct_eco_kills': 0.0, 'ct_round_swing': 0.0,
            't_rounds': 0, 'ct_rounds': 0
        })

        TRADE_TICK_WINDOW = 64 * 4  # 4 seconds
        team1_score = 0
        team2_score = 0
        round_history = []

        # 6. Process rounds, duels, clutches, and round swing
        for r_num in range(1, total_rounds + 1):
            r_row = rounds_df[rounds_df['round_num'] == r_num].iloc[0]
            r_deaths = deaths[deaths['round_num'] == r_num].sort_values('tick').reset_index(drop=True)
            r_winner = r_row['winner']
            r_reason = r_row['reason']

            # Alive players at round start
            alive_players = set(all_players)
            death_records = []
            round_kills = defaultdict(int)
            round_swings = defaultdict(float)

            # Assign round to sides
            for p in all_players:
                p_side = get_player_side_in_round(p, r_num)
                if p_side == 'T':
                    stats[p]['t_rounds'] += 1
                else:
                    stats[p]['ct_rounds'] += 1

            # Determine Team 1's side in this round
            t1_sample = list(team1_players)[0]
            t1_side_this_round = get_player_side_in_round(t1_sample, r_num)

            # Score update
            if r_winner == t1_side_this_round:
                team1_score += 1
            else:
                team2_score += 1

            # Count of alive players for WP
            ct_alive = 5
            t_alive = 5

            # Clutch tracking variables
            # Track who enters 1vX
            clutch_situation = None # (player, side, opp_count)

            for d_idx, d_row in r_deaths.iterrows():
                atk = d_row['attacker_name']
                vic = d_row['user_name']
                d_tick = d_row['tick']
                is_hs = bool(d_row.get('headshot', False))

                if atk in all_players and vic in all_players and atk != vic:
                    atk_side = get_player_side_in_round(atk, r_num)
                    vic_side = get_player_side_in_round(vic, r_num)

                    # Opening Duel
                    if d_idx == 0:
                        stats[atk]['opening_kills'] += 1
                        stats[vic]['opening_deaths'] += 1

                    if is_hs:
                        stats[atk]['headshots'] += 1

                    # Sniper / AWP weapon detection from real kill event
                    wpn = str(d_row.get('weapon', '')).lower()
                    if 'awp' in wpn:
                        stats[atk]['awp_kills'] += 1
                        stats[atk]['sniper_kills'] += 1
                        stats[atk]['rounds_with_awp'].add(r_num)
                        stats[atk]['rounds_with_sniper'].add(r_num)
                    elif any(s_w in wpn for s_w in ('ssg08', 'scout', 'scar20', 'g3sg1')):
                        stats[atk]['sniper_kills'] += 1
                        stats[atk]['rounds_with_sniper'].add(r_num)

                    # 1. Eco-Adjustment
                    atk_val = d_row['attacker_current_equip_value']
                    vic_val = d_row['user_current_equip_value']
                    arm_val = d_row['user_armor_value']

                    atk_cost = float(atk_val) if pd.notnull(atk_val) and float(atk_val) > 0 else 2000.0
                    vic_cost = float(vic_val) if pd.notnull(vic_val) and float(vic_val) > 0 else 2000.0
                    vic_armor = float(arm_val) if pd.notnull(arm_val) else 0.0

                    atk_cost = max(atk_cost, 500.0)
                    vic_cost = max(vic_cost, 500.0)

                    raw_ratio = vic_cost / atk_cost
                    eco_mult = float(np.clip(raw_ratio ** 0.5, 0.4, 2.2))

                    # Penalize anti-eco padding
                    if vic_armor == 0 and vic_cost < 1500:
                        eco_mult *= 0.65
                        stats[atk]['anti_eco_kills'] += 1
                    # Reward high-pressure upset kills
                    if atk_cost < 1500 and vic_cost >= 4500:
                        eco_mult *= 1.35
                        stats[atk]['pressure_kills'] += 1

                    kill_eco_value = 1.0 * eco_mult

                    # 2. Round Swing (Win Probability Delta)
                    my_team_alive = ct_alive if atk_side == 'CT' else t_alive
                    opp_team_alive = t_alive if atk_side == 'CT' else ct_alive

                    wp_before = get_wp(my_team_alive, opp_team_alive)
                    wp_after = get_wp(my_team_alive, opp_team_alive - 1)
                    delta_wp = max(0.0, wp_after - wp_before)

                    # Penalty for exit hunting during save
                    if opp_team_alive == 1 and my_team_alive >= 3 and delta_wp < 0.05:
                        delta_wp *= 0.20

                    assister = d_row['assister_name']
                    if assister in all_players and assister != atk:
                        k_swing = delta_wp * 0.70
                        a_swing = delta_wp * 0.30
                        stats[assister]['round_swing'] += a_swing
                        round_swings[assister] += a_swing
                        if get_player_side_in_round(assister, r_num) == 'T':
                            stats[assister]['t_round_swing'] += a_swing
                        else:
                            stats[assister]['ct_round_swing'] += a_swing
                    else:
                        k_swing = delta_wp

                    stats[atk]['round_swing'] += k_swing
                    round_swings[atk] += k_swing
                    if atk_side == 'T':
                        stats[atk]['t_round_swing'] += k_swing
                        stats[atk]['t_kills'] += 1
                        stats[atk]['t_eco_kills'] += kill_eco_value
                    else:
                        stats[atk]['ct_round_swing'] += k_swing
                        stats[atk]['ct_kills'] += 1
                        stats[atk]['ct_eco_kills'] += kill_eco_value

                    # Update alive counts
                    if vic_side == 'CT':
                        ct_alive = max(0, ct_alive - 1)
                        stats[vic]['ct_deaths'] += 1
                    else:
                        t_alive = max(0, t_alive - 1)
                        stats[vic]['t_deaths'] += 1

                    stats[atk]['kills'] += 1
                    stats[atk]['eco_kills'] += kill_eco_value
                    stats[atk]['rounds_with_kill'].add(r_num)
                    round_kills[atk] += 1

                    stats[vic]['deaths'] += 1
                    death_records.append({'tick': d_tick, 'victim': vic, 'killer': atk, 'vic_side': vic_side})
                    alive_players.discard(vic)

                    if assister in all_players and assister != atk:
                        stats[assister]['assists'] += 1
                        stats[assister]['rounds_with_assist'].add(r_num)

                    # Clutch Detection
                    # Check if victim's death caused someone to enter 1vX
                    # Remaining alive on CT and T
                    remaining_ct = [p for p in alive_players if get_player_side_in_round(p, r_num) == 'CT']
                    remaining_t = [p for p in alive_players if get_player_side_in_round(p, r_num) == 'T']

                    if len(remaining_ct) == 1 and len(remaining_t) >= 1 and clutch_situation is None:
                        clutch_situation = (remaining_ct[0], 'CT', len(remaining_t))
                    elif len(remaining_t) == 1 and len(remaining_ct) >= 1 and clutch_situation is None:
                        clutch_situation = (remaining_t[0], 'T', len(remaining_ct))

            # Evaluate Clutch Won
            if clutch_situation is not None:
                clutcher, c_side, opp_cnt = clutch_situation
                if r_winner == c_side:
                    # Successfully clutched!
                    if opp_cnt == 1: stats[clutcher]['clutch_1v1'] += 1
                    elif opp_cnt == 2: stats[clutcher]['clutch_1v2'] += 1
                    elif opp_cnt == 3: stats[clutcher]['clutch_1v3'] += 1
                    elif opp_cnt == 4: stats[clutcher]['clutch_1v4'] += 1
                    elif opp_cnt >= 5: stats[clutcher]['clutch_1v5'] += 1

            # Multi-kills
            for p, k_cnt in round_kills.items():
                if k_cnt == 2: stats[p]['multi_2k'] += 1
                elif k_cnt == 3: stats[p]['multi_3k'] += 1
                elif k_cnt == 4: stats[p]['multi_4k'] += 1
                elif k_cnt >= 5: stats[p]['multi_5k'] += 1

            for p in alive_players:
                stats[p]['rounds_survived'].add(r_num)

            # Trades check (both Traded Deaths and Trade Kills within 4 seconds)
            for dr in death_records:
                vic = dr['victim']
                vic_killer = dr['killer']
                v_tick = dr['tick']
                for follow_dr in death_records:
                    if follow_dr['victim'] == vic_killer and (0 < follow_dr['tick'] - v_tick <= TRADE_TICK_WINDOW):
                        if get_player_side_in_round(follow_dr['killer'], r_num) == dr['vic_side']:
                            stats[vic]['rounds_traded_death'].add(r_num)
                            stats[follow_dr['killer']]['trade_kills'] += 1
                            break

            # Round timeline summary
            top_swinger = max(round_swings.items(), key=lambda x: x[1])[0] if round_swings else "None"
            top_swinger_val = round(round_swings.get(top_swinger, 0.0) * 100, 1)

            half_label = "1-я половина" if r_num <= 12 else ("2-я половина" if r_num <= 24 else "Овертайм")
            round_history.append({
                'round_num': r_num,
                'half': half_label,
                'winner': r_winner,
                'reason': r_reason,
                'score': f"{team1_score}:{team2_score}",
                'top_swing_player': top_swinger,
                'top_swing_val': f"+{top_swinger_val}%"
            })

        # Damage calculation from hurt events (STRICT 100 HP CAP PER VICTIM PER ROUND)
        # В CS2 сырой dmg_health при попадании в голову с AWP равен 447.
        # Ограничиваем суммарный полученный жертвой урон в рамках одного раунда до 100 HP (стандарт HLTV/CS2).
        victim_hp_lost_in_round = defaultdict(float)
        hurt_sorted = hurt.sort_values('tick')
        for _, h_row in hurt_sorted.iterrows():
            atk = h_row['attacker_name']
            vic = h_row['user_name']
            raw_dmg = max(0.0, float(h_row['dmg_health']))
            r_num = int(h_row['round_num'])
            wpn = str(h_row.get('weapon', '')).lower()

            if atk in all_players and vic in all_players and atk != vic:
                already_lost = victim_hp_lost_in_round[(r_num, vic)]
                remaining_hp = max(0.0, 100.0 - already_lost)
                dmg = min(raw_dmg, remaining_hp)
                if dmg <= 0.0:
                    continue
                victim_hp_lost_in_round[(r_num, vic)] += dmg

                atk_side = get_player_side_in_round(atk, r_num)
                stats[atk]['damage'] += dmg
                if atk_side == 'T':
                    stats[atk]['t_damage'] += dmg
                else:
                    stats[atk]['ct_damage'] += dmg

                if any(u_w in wpn for u_w in ('hegrenade', 'inferno', 'molotov', 'incgrenade', 'flashbang', 'decoy')):
                    stats[atk]['utility_damage'] += dmg

                arm_val = h_row['user_armor_value']
                eq_val = h_row['user_current_equip_value']
                vic_armor = float(arm_val) if pd.notnull(arm_val) else 0.0
                vic_cost = float(eq_val) if pd.notnull(eq_val) and float(eq_val) > 0 else 2000.0
                vic_cost = max(vic_cost, 500.0)

                dmg_weight = float(np.clip((vic_cost / 3000.0) ** 0.35, 0.5, 1.3))
                if vic_armor == 0:
                    dmg_weight *= 0.75
                stats[atk]['eco_damage'] += dmg * dmg_weight

        # Real Grenade & Utility Events Extraction from .dem
        grenade_event_map = [
            ('smokegrenade_detonate', 'smokes_thrown'),
            ('flashbang_detonate', 'flashes_thrown'),
            ('inferno_startburn', 'molotovs_thrown'),
            ('hegrenade_detonate', 'he_thrown'),
        ]
        for ev_name, stat_key in grenade_event_map:
            try:
                ev_df = parser.parse_event(ev_name)
                if len(ev_df) > 0 and 'tick' in ev_df.columns:
                    ev_df = ev_df[ev_df['tick'].between(min_tick, max_tick)]
                    for _, g_row in ev_df.iterrows():
                        u_name = g_row.get('user_name')
                        if u_name in all_players and get_round_idx(g_row['tick']) is not None:
                            stats[u_name][stat_key] += 1
            except Exception:
                pass

        try:
            blind_df = parser.parse_event('player_blind')
            if len(blind_df) > 0 and 'tick' in blind_df.columns:
                blind_df = blind_df[blind_df['tick'].between(min_tick, max_tick)]
                for _, b_row in blind_df.iterrows():
                    atk = b_row.get('attacker_name')
                    vic = b_row.get('user_name')
                    dur = float(b_row.get('blind_duration', 0.0) or 0.0)
                    r_idx = get_round_idx(b_row['tick'])
                    if r_idx is not None and atk in all_players and vic in all_players and atk != vic and dur >= 1.0:
                        if get_player_side_in_round(atk, r_idx) != get_player_side_in_round(vic, r_idx):
                            stats[atk]['enemies_flashed'] += 1
        except Exception:
            pass

        # KAST%
        for p in all_players:
            kast_rounds = set()
            for r in range(1, total_rounds + 1):
                if (r in stats[p]['rounds_with_kill'] or
                    r in stats[p]['rounds_with_assist'] or
                    r in stats[p]['rounds_survived'] or
                    r in stats[p]['rounds_traded_death']):
                    kast_rounds.add(r)
            stats[p]['kast_pct'] = len(kast_rounds) / total_rounds * 100.0

            t_r = max(1, stats[p]['t_rounds'])
            ct_r = max(1, stats[p]['ct_rounds'])
            t_kast = sum(1 for r in kast_rounds if get_player_side_in_round(p, r) == 'T')
            ct_kast = sum(1 for r in kast_rounds if get_player_side_in_round(p, r) == 'CT')
            stats[p]['t_kast_pct'] = (t_kast / t_r * 100.0)
            stats[p]['ct_kast_pct'] = (ct_kast / ct_r * 100.0)

        # 7. Impact with Clutches and Sub-ratings calculation
        summary = []
        for p in all_players:
            s = stats[p]
            kpr = s['kills'] / total_rounds
            dpr = s['deaths'] / total_rounds
            eco_kpr = s['eco_kills'] / total_rounds
            eco_adr = s['eco_damage'] / total_rounds
            adr = s['damage'] / total_rounds
            swing_pr = s['round_swing'] / total_rounds
            survival_rate = (total_rounds - s['deaths']) / total_rounds

            # Clutches score: 1v1 (1.0), 1v2 (2.5), 1v3 (4.5), 1v4 (7.0), 1v5 (10.0)
            clutch_points = (s['clutch_1v1'] * 1.0 +
                             s['clutch_1v2'] * 2.5 +
                             s['clutch_1v3'] * 4.5 +
                             s['clutch_1v4'] * 7.0 +
                             s['clutch_1v5'] * 10.0)

            # Impact Rating Formula (HLTV + Clutches)
            impact = (2.13 * kpr +
                      0.42 * (s['assists'] / total_rounds) - 0.41 +
                      (s['opening_kills'] / total_rounds) * 0.45 +
                      (s['multi_2k']*0.06 + s['multi_3k']*0.18 + s['multi_4k']*0.35 + s['multi_5k']*0.60) +
                      (clutch_points / total_rounds) * 1.20)

            summary.append({
                'player': p,
                'team': team1_name if p in team1_players else team2_name,
                'k': s['kills'], 'd': s['deaths'], 'a': s['assists'], 'hs': s['headshots'],
                'diff': s['kills'] - s['deaths'],
                'adr': adr, 'kpr': kpr, 'dpr': dpr,
                'eco_k': s['eco_kills'], 'eco_kpr': eco_kpr,
                'eco_adr': eco_adr, 'round_swing': s['round_swing'], 'swing_pr': swing_pr,
                'survival_rate': survival_rate, 'kast_pct': s['kast_pct'],
                'impact': impact, 'ok': s['opening_kills'], 'od': s['opening_deaths'],
                'pressure_kills': s['pressure_kills'], 'anti_eco_kills': s['anti_eco_kills'],
                'm2': s['multi_2k'], 'm3': s['multi_3k'], 'm4': s['multi_4k'],
                'c1v1': s['clutch_1v1'], 'c1v2': s['clutch_1v2'], 'c1v3': s['clutch_1v3'],
                'c1v4': s['clutch_1v4'], 'c1v5': s['clutch_1v5'],
                'clutch_points': clutch_points,
                'traded_deaths': len(s['rounds_traded_death'])
            })

        sum_df = pd.DataFrame(summary)

        # Baseline means
        avg_eco_kpr = sum_df['eco_kpr'].mean()
        avg_survival = sum_df['survival_rate'].mean()
        avg_eco_adr = sum_df['eco_adr'].mean()
        avg_swing_pr = sum_df['swing_pr'].mean()
        avg_impact = sum_df['impact'].mean()
        avg_kast = sum_df['kast_pct'].mean()

        player_cards = []
        for _, r in sum_df.iterrows():
            p = r['player']
            s = stats[p]

            r_kill_eco = r['eco_kpr'] / avg_eco_kpr
            r_survival = r['survival_rate'] / avg_survival
            r_damage_eco = r['eco_adr'] / avg_eco_adr
            r_roundswing = r['swing_pr'] / avg_swing_pr
            r_impact = r['impact'] / avg_impact
            r_kast = r['kast_pct'] / avg_kast

            # LanUs 3.0 (v4.0 Final Formula)
            lanus_3 = (0.25 * r_kill_eco +
                       0.15 * r_survival +
                       0.20 * r_damage_eco +
                       0.20 * r_roundswing +
                       0.10 * r_impact +
                       0.10 * r_kast)

            t_rnd = max(1, s['t_rounds'])
            ct_rnd = max(1, s['ct_rounds'])

            t_rating = max(0.2, (0.25 * ((s['t_eco_kills'] / t_rnd) / avg_eco_kpr) +
                                 0.15 * (((t_rnd - s['t_deaths']) / t_rnd) / avg_survival) +
                                 0.20 * (((s['t_damage'] / t_rnd) / avg_eco_adr)) +
                                 0.20 * (((s['t_round_swing'] / t_rnd) / avg_swing_pr)) +
                                 0.10 * ((2.13 * (s['t_kills'] / t_rnd) - 0.41) / avg_impact) +
                                 0.10 * (s['t_kast_pct'] / avg_kast)))

            ct_rating = max(0.2, (0.25 * ((s['ct_eco_kills'] / ct_rnd) / avg_eco_kpr) +
                                  0.15 * (((ct_rnd - s['ct_deaths']) / ct_rnd) / avg_survival) +
                                  0.20 * (((s['ct_damage'] / ct_rnd) / avg_eco_adr)) +
                                  0.20 * (((s['ct_round_swing'] / ct_rnd) / avg_swing_pr)) +
                                  0.10 * ((2.13 * (s['ct_kills'] / ct_rnd) - 0.41) / avg_impact) +
                                  0.10 * (s['ct_kast_pct'] / avg_kast)))

            hs_pct = round(r['hs'] / max(1, r['k']) * 100.0, 1)
            eco_delta = r['eco_k'] - r['k']
            eco_delta_str = f"+{round(eco_delta, 1)}" if eco_delta >= 0 else str(round(eco_delta, 1))

            clutch_parts = []
            if r['c1v1'] > 0: clutch_parts.append(f"1v1: {r['c1v1']}")
            if r['c1v2'] > 0: clutch_parts.append(f"1v2: {r['c1v2']}")
            if r['c1v3'] > 0: clutch_parts.append(f"1v3: {r['c1v3']}")
            if r['c1v4'] > 0: clutch_parts.append(f"1v4: {r['c1v4']}")
            if r['c1v5'] > 0: clutch_parts.append(f"1v5: {r['c1v5']}")
            clutches_str = ", ".join(clutch_parts) if clutch_parts else "—"

            # HLTV 3.0 Per-Round Net Win Probability Swing (typically between -5.0% and +6.0% per round)
            net_swing_per_round_pct = float(
                np.clip((r['swing_pr'] - (r['dpr'] * 0.145)) * 38.0, -7.5, 7.5)
            )
            net_swing_rounded = round(net_swing_per_round_pct, 2)
            net_swing_str = f"{net_swing_rounded:+.2f}%"

            player_cards.append({
                'player': p,
                'steam_id': player_to_steamid.get(p, ''),
                'team': r['team'],
                'k_d': f"{r['k']}-{r['d']}",
                'k': int(r['k']),
                'd': int(r['d']),
                'a': int(r['a']),
                'hs_pct': f"{hs_pct}%",
                'diff': int(r['diff']),
                'diff_str': f"+{r['diff']}" if r['diff'] > 0 else str(r['diff']),
                'adr': round(r['adr'], 1),
                'kast_pct': round(r['kast_pct'], 1),
                'eco_kills': round(r['eco_k'], 1),
                'eco_kills_impact': f"{round(r['eco_k'], 1)} ({eco_delta_str})",
                'eco_delta': round(eco_delta, 2),
                'round_swing': net_swing_rounded,
                'round_swing_str': net_swing_str,
                'lanus_3': round(lanus_3, 2),
                't_rating': round(t_rating, 2),
                'ct_rating': round(ct_rating, 2),
                'ok_od': f"{r['ok']}-{r['od']}",
                'opening_kills': int(r['ok']),
                'opening_deaths': int(r['od']),
                'pressure_kills': int(r['pressure_kills']),
                'anti_eco_kills': int(r['anti_eco_kills']),
                'multi_2k': int(r['m2']),
                'multi_3k': int(r['m3']),
                'multi_4k': int(r['m4']),
                'clutches_str': clutches_str,
                'clutch_points': round(r['clutch_points'], 1),
                'traded_deaths': int(r['traded_deaths']),
                'trade_kills': int(s['trade_kills']),
                'trade_pct': round(r['traded_deaths'] / max(1, r['d']) * 100.0, 1),
                'awp_kills': int(s['awp_kills']),
                'sniper_kills': int(s['sniper_kills']),
                'rounds_with_awp': len(s['rounds_with_awp']),
                'rounds_with_sniper': len(s['rounds_with_sniper']),
                'smokes_thrown': int(s['smokes_thrown']),
                'flashes_thrown': int(s['flashes_thrown']),
                'molotovs_thrown': int(s['molotovs_thrown']),
                'he_thrown': int(s['he_thrown']),
                'enemies_flashed': int(s['enemies_flashed']),
                'utility_damage': round(s['utility_damage'], 1),
                'impact': round(r['impact'], 2),
                'sub_ratings': {
                    'r_kill_eco': round(r_kill_eco, 2),
                    'r_survival': round(r_survival, 2),
                    'r_damage_eco': round(r_damage_eco, 2),
                    'r_roundswing': round(r_roundswing, 2),
                    'r_impact': round(r_impact, 2),
                    'r_kast': round(r_kast, 2)
                }
            })

        player_cards = sorted(player_cards, key=lambda x: x['lanus_3'], reverse=True)

        mvp = player_cards[0]
        evp1 = player_cards[1]
        evp2 = player_cards[2]
        evp3 = player_cards[3] if len(player_cards) > 3 else None

        return {
            'map_name': map_name,
            'server_name': server_name,
            'total_rounds': total_rounds,
            'team1_name': team1_name,
            'team2_name': team2_name,
            'final_score': f"{team1_score}:{team2_score}",
            'filter_message': filter_message,
            'mvp': mvp,
            'evps': [evp1, evp2, evp3] if evp3 else [evp1, evp2],
            'players': player_cards,
            'round_history': round_history
        }
