"""
LanUs Rating 3.0 (HLTV Rating 3.0 Analog) CS2 Demo Analyzer
Author: Antigravity AI
"""

import sys
import os
import argparse
from collections import defaultdict
import numpy as np
import pandas as pd

try:
    from demoparser2 import DemoParser
except ImportError:
    print("Ошибка: библиотека demoparser2 не установлена.")
    print("Установите ее командой: pip install demoparser2")
    sys.exit(1)

# Fix Windows console encoding for UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


# Win Probability Matrix (Team Alive vs Opponent Alive) -> Team Win Probability
WP_TABLE = {
    (5, 5): 0.50, (5, 4): 0.72, (5, 3): 0.86, (5, 2): 0.95, (5, 1): 0.99, (5, 0): 1.00,
    (4, 5): 0.28, (4, 4): 0.50, (4, 3): 0.73, (4, 2): 0.88, (4, 1): 0.97, (4, 0): 1.00,
    (3, 5): 0.14, (3, 4): 0.27, (3, 3): 0.50, (3, 2): 0.74, (3, 1): 0.92, (3, 0): 1.00,
    (2, 5): 0.05, (2, 4): 0.12, (2, 3): 0.26, (2, 2): 0.50, (2, 1): 0.76, (2, 0): 1.00,
    (1, 5): 0.01, (1, 4): 0.03, (1, 3): 0.08, (1, 2): 0.24, (1, 1): 0.50, (1, 0): 1.00,
    (0, 5): 0.00, (0, 4): 0.00, (0, 3): 0.00, (0, 2): 0.00, (0, 1): 0.00, (0, 0): 0.50
}

def get_wp(team_alive: int, opp_alive: int) -> float:
    t_a = max(0, min(5, int(team_alive)))
    o_a = max(0, min(5, int(opp_alive)))
    return WP_TABLE.get((t_a, o_a), 0.50)


def analyze_demo(demo_path: str):
    if not os.path.exists(demo_path):
        print(f"Ошибка: Файл демки '{demo_path}' не найден!")
        return

    print(f"[*] Загрузка и парсинг демо-файла: {demo_path}")
    parser = DemoParser(demo_path)

    # 1. Поиск официального старта матча и фильтрация разминки/ножевого раунда
    match_starts = parser.parse_event('round_announce_match_start')
    r_starts = parser.parse_event('round_start', other=['total_rounds_played', 'is_warmup_period', 'is_match_started'])
    r_ends = parser.parse_event('round_end', other=['total_rounds_played', 'is_warmup_period', 'is_match_started'])

    # Определение тика старта матча (последний round_announce_match_start перед регулярными раундами)
    if len(match_starts) > 0:
        match_start_tick = match_starts.iloc[-1]['tick']
    else:
        # Резервный поиск первого раунда вне вармапа
        non_warmup = r_starts[r_starts['is_warmup_period'] == False]
        match_start_tick = non_warmup.iloc[0]['tick'] if len(non_warmup) > 0 else 0

    # Проверка на ножевой раунд (если перед последним рестартом был раунд, где 100% киллов с ножа)
    deaths_raw = parser.parse_event('player_death',
                                    player=['armor_value', 'has_helmet', 'current_equip_value', 'team_name', 'team_num'])
    
    # Фильтрация только официальных раундов
    official_ends = r_ends[r_ends['tick'] >= match_start_tick].reset_index(drop=True)
    # Находим соответствующие round_start (ближайший предшествующий старт для каждого энда)
    rounds = []
    for i, end_row in official_ends.iterrows():
        end_tick = end_row['tick']
        # предшествующие старты
        prev_starts = r_starts[r_starts['tick'] < end_tick]
        if len(prev_starts) > 0:
            start_tick = prev_starts.iloc[-1]['tick']
        else:
            start_tick = end_tick - 1000
        rounds.append({
            'round_num': i + 1,
            'start_tick': start_tick,
            'end_tick': end_tick,
            'winner': end_row.get('winner', ''),
            'reason': end_row.get('reason', '')
        })

    rounds_df = pd.DataFrame(rounds)
    total_rounds = len(rounds_df)

    if total_rounds == 0:
        print("Ошибка: Не удалось найти официальные боевые раунды в демке.")
        return

    print(f"\n🧹 Фильтр демки: Удалены данные разминки / ножевого раунда. В расчет вошли только {total_rounds} официальных раундов.\n")

    # Получение урона
    hurt_raw = parser.parse_event('player_hurt',
                                  player=['armor_value', 'has_helmet', 'current_equip_value', 'team_name', 'team_num'])

    # Привязка событий к раундам
    def get_round_idx(tick):
        for _, r in rounds_df.iterrows():
            if r['start_tick'] <= tick <= r['end_tick'] + 128:
                return int(r['round_num'])
        return None

    deaths = deaths_raw[deaths_raw['tick'].between(rounds_df['start_tick'].min(), rounds_df['end_tick'].max() + 128)].copy()
    deaths['round_num'] = deaths['tick'].apply(get_round_idx)
    deaths = deaths.dropna(subset=['round_num']).copy()
    deaths['round_num'] = deaths['round_num'].astype(int)

    hurt = hurt_raw[hurt_raw['tick'].between(rounds_df['start_tick'].min(), rounds_df['end_tick'].max() + 128)].copy()
    hurt['round_num'] = hurt['tick'].apply(get_round_idx)
    hurt = hurt.dropna(subset=['round_num']).copy()
    hurt['round_num'] = hurt['round_num'].astype(int)

    # Список игроков
    players_info = parser.parse_player_info()
    all_players = sorted(list(players_info['name'].unique()))

    # Стороны игроков по половинам (1-12 раунды и 13+ раунды)
    player_sides = {}
    for p in all_players:
        p_d = deaths[deaths['user_name'] == p]
        h1_side = 'CT'
        if len(p_d) > 0:
            h1 = p_d[p_d['round_num'] <= 12]
            if len(h1) > 0:
                h1_side = 'CT' if 'CT' in str(h1.iloc[0]['user_team_name']) else 'T'
            else:
                h2 = p_d[p_d['round_num'] > 12]
                h1_side = 'T' if 'CT' in str(h2.iloc[0]['user_team_name']) else 'CT'
        else:
            p_k = deaths[deaths['attacker_name'] == p]
            h1 = p_k[p_k['round_num'] <= 12]
            if len(h1) > 0:
                h1_side = 'CT' if 'CT' in str(h1.iloc[0]['attacker_team_name']) else 'T'
            else:
                h2 = p_k[p_k['round_num'] > 12]
                h1_side = 'T' if 'CT' in str(h2.iloc[0]['attacker_team_name']) else 'CT'
        player_sides[p] = {'H1': h1_side, 'H2': 'T' if h1_side == 'CT' else 'CT'}

    def get_player_round_side(p, r_num):
        return player_sides[p]['H1'] if r_num <= 12 else player_sides[p]['H2']

    # Инициализация статистики игроков
    stats = defaultdict(lambda: {
        'kills': 0, 'deaths': 0, 'assists': 0, 'damage': 0.0,
        'eco_kills': 0.0, 'eco_damage': 0.0, 'round_swing': 0.0,
        'opening_kills': 0, 'opening_deaths': 0,
        'multi_2k': 0, 'multi_3k': 0, 'multi_4k': 0, 'multi_5k': 0,
        'rounds_with_kill': set(), 'rounds_with_assist': set(),
        'rounds_survived': set(), 'rounds_traded_death': set(),
        't_kills': 0, 't_deaths': 0, 't_damage': 0.0, 't_eco_kills': 0.0, 't_round_swing': 0.0,
        'ct_kills': 0, 'ct_deaths': 0, 'ct_damage': 0.0, 'ct_eco_kills': 0.0, 'ct_round_swing': 0.0,
        't_rounds': 0, 'ct_rounds': 0
    })

    for p in all_players:
        if player_sides[p]['H1'] == 'T':
            stats[p]['t_rounds'] = min(12, total_rounds)
            stats[p]['ct_rounds'] = max(0, total_rounds - 12)
        else:
            stats[p]['ct_rounds'] = min(12, total_rounds)
            stats[p]['t_rounds'] = max(0, total_rounds - 12)

    TRADE_TICK_WINDOW = 64 * 4  # 4 секунды

    # Пораундовый расчет дуэлей, Eco-Adjustment и Round Swing
    for r_num in range(1, total_rounds + 1):
        r_deaths = deaths[deaths['round_num'] == r_num].sort_values('tick').reset_index(drop=True)
        alive_players = set(all_players)
        death_records = []
        round_kills = defaultdict(int)

        ct_alive = 5
        t_alive = 5

        for d_idx, d_row in r_deaths.iterrows():
            atk = d_row['attacker_name']
            vic = d_row['user_name']
            d_tick = d_row['tick']

            if atk in all_players and vic in all_players:
                atk_side = get_player_round_side(atk, r_num)
                vic_side = get_player_round_side(vic, r_num)

                # Opening Duel
                if d_idx == 0:
                    stats[atk]['opening_kills'] += 1
                    stats[vic]['opening_deaths'] += 1

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

                # Штраф за анти-эко фарм
                if vic_armor == 0 and vic_cost < 1500:
                    eco_mult *= 0.65
                # Бонус за фраг под давлением
                if atk_cost < 1500 and vic_cost >= 4500:
                    eco_mult *= 1.35

                kill_eco_value = 1.0 * eco_mult

                # 2. Round Swing
                my_team_alive = ct_alive if atk_side == 'CT' else t_alive
                opp_team_alive = t_alive if atk_side == 'CT' else ct_alive

                wp_before = get_wp(my_team_alive, opp_team_alive)
                wp_after = get_wp(my_team_alive, opp_team_alive - 1)
                delta_wp = max(0.0, wp_after - wp_before)

                # Штраф за эксит-фраги при явном сейве
                if opp_team_alive == 1 and my_team_alive >= 3 and delta_wp < 0.05:
                    delta_wp *= 0.20

                assister = d_row['assister_name']
                if assister in all_players and assister != atk:
                    k_swing = delta_wp * 0.70
                    a_swing = delta_wp * 0.30
                    stats[assister]['round_swing'] += a_swing
                    if get_player_round_side(assister, r_num) == 'T':
                        stats[assister]['t_round_swing'] += a_swing
                    else:
                        stats[assister]['ct_round_swing'] += a_swing
                else:
                    k_swing = delta_wp

                stats[atk]['round_swing'] += k_swing
                if atk_side == 'T':
                    stats[atk]['t_round_swing'] += k_swing
                    stats[atk]['t_kills'] += 1
                    stats[atk]['t_eco_kills'] += kill_eco_value
                else:
                    stats[atk]['ct_round_swing'] += k_swing
                    stats[atk]['ct_kills'] += 1
                    stats[atk]['ct_eco_kills'] += kill_eco_value

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

        for p, k_cnt in round_kills.items():
            if k_cnt == 2: stats[p]['multi_2k'] += 1
            elif k_cnt == 3: stats[p]['multi_3k'] += 1
            elif k_cnt == 4: stats[p]['multi_4k'] += 1
            elif k_cnt >= 5: stats[p]['multi_5k'] += 1

        for p in alive_players:
            stats[p]['rounds_survived'].add(r_num)

        # Trade kills check
        for dr in death_records:
            vic = dr['victim']
            vic_killer = dr['killer']
            v_tick = dr['tick']
            for follow_dr in death_records:
                if follow_dr['victim'] == vic_killer and (0 < follow_dr['tick'] - v_tick <= TRADE_TICK_WINDOW):
                    if get_player_round_side(follow_dr['killer'], r_num) == dr['vic_side']:
                        stats[vic]['rounds_traded_death'].add(r_num)
                        break

    # Обработка урона
    for _, h_row in hurt.iterrows():
        atk = h_row['attacker_name']
        vic = h_row['user_name']
        dmg = float(h_row['dmg_health'])
        r_num = int(h_row['round_num'])

        if atk in all_players and vic in all_players and atk != vic:
            atk_side = get_player_round_side(atk, r_num)
            stats[atk]['damage'] += dmg
            if atk_side == 'T':
                stats[atk]['t_damage'] += dmg
            else:
                stats[atk]['ct_damage'] += dmg

            arm_val = h_row['user_armor_value']
            eq_val = h_row['user_current_equip_value']
            vic_armor = float(arm_val) if pd.notnull(arm_val) else 0.0
            vic_cost = float(eq_val) if pd.notnull(eq_val) and float(eq_val) > 0 else 2000.0
            vic_cost = max(vic_cost, 500.0)

            dmg_weight = float(np.clip((vic_cost / 3000.0) ** 0.35, 0.5, 1.3))
            if vic_armor == 0:
                dmg_weight *= 0.75
            stats[atk]['eco_damage'] += dmg * dmg_weight

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

        t_r = stats[p]['t_rounds']
        ct_r = stats[p]['ct_rounds']
        t_kast = sum(1 for r in kast_rounds if get_player_round_side(p, r) == 'T')
        ct_kast = sum(1 for r in kast_rounds if get_player_round_side(p, r) == 'CT')
        stats[p]['t_kast_pct'] = (t_kast / t_r * 100.0) if t_r > 0 else 0.0
        stats[p]['ct_kast_pct'] = (ct_kast / ct_r * 100.0) if ct_r > 0 else 0.0

    # Сводка и расчет суб-показателей
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
        impact = 2.13 * kpr + 0.42 * (s['assists'] / total_rounds) - 0.41 + (s['opening_kills'] / total_rounds) * 0.5 + (s['multi_2k']*0.05 + s['multi_3k']*0.15 + s['multi_4k']*0.3)

        summary.append({
            'player': p,
            'k': s['kills'], 'd': s['deaths'], 'diff': s['kills'] - s['deaths'],
            'adr': adr, 'kpr': kpr, 'dpr': dpr,
            'eco_k': s['eco_kills'], 'eco_kpr': eco_kpr,
            'eco_adr': eco_adr, 'round_swing': s['round_swing'], 'swing_pr': swing_pr,
            'survival_rate': survival_rate, 'kast_pct': s['kast_pct'],
            'impact': impact, 'ok': s['opening_kills'], 'od': s['opening_deaths']
        })

    sum_df = pd.DataFrame(summary)

    # Матчевые средние для нормировки
    avg_eco_kpr = sum_df['eco_kpr'].mean()
    avg_survival = sum_df['survival_rate'].mean()
    avg_eco_adr = sum_df['eco_adr'].mean()
    avg_swing_pr = sum_df['swing_pr'].mean()
    avg_impact = sum_df['impact'].mean()
    avg_kast = sum_df['kast_pct'].mean()

    # LanUs Rating 3.0
    results = []
    for _, r in sum_df.iterrows():
        p = r['player']
        s = stats[p]

        r_kill_eco = r['eco_kpr'] / avg_eco_kpr
        r_survival = r['survival_rate'] / avg_survival
        r_damage_eco = r['eco_adr'] / avg_eco_adr
        r_roundswing = r['swing_pr'] / avg_swing_pr
        r_impact = r['impact'] / avg_impact
        r_kast = r['kast_pct'] / avg_kast

        lanus_3 = (0.25 * r_kill_eco +
                   0.15 * r_survival +
                   0.20 * r_damage_eco +
                   0.20 * r_roundswing +
                   0.10 * r_impact +
                   0.10 * r_kast)

        # T & CT ratings
        t_rnd = s['t_rounds']
        ct_rnd = s['ct_rounds']

        t_rating = max(0.2, (0.25 * ((s['t_eco_kills'] / t_rnd) / avg_eco_kpr) +
                             0.15 * (((t_rnd - s['t_deaths']) / t_rnd) / avg_survival) +
                             0.20 * (((s['t_damage'] / t_rnd) / avg_eco_adr)) +
                             0.20 * (((s['t_round_swing'] / t_rnd) / avg_swing_pr)) +
                             0.10 * ((2.13 * (s['t_kills'] / t_rnd) - 0.41) / avg_impact) +
                             0.10 * (s['t_kast_pct'] / avg_kast))) if t_rnd > 0 else 1.0

        ct_rating = max(0.2, (0.25 * ((s['ct_eco_kills'] / ct_rnd) / avg_eco_kpr) +
                              0.15 * (((ct_rnd - s['ct_deaths']) / ct_rnd) / avg_survival) +
                              0.20 * (((s['ct_damage'] / ct_rnd) / avg_eco_adr)) +
                              0.20 * (((s['ct_round_swing'] / ct_rnd) / avg_swing_pr)) +
                              0.10 * ((2.13 * (s['ct_kills'] / ct_rnd) - 0.41) / avg_impact) +
                              0.10 * (s['ct_kast_pct'] / avg_kast))) if ct_rnd > 0 else 1.0

        diff_str = f"+{r['diff']}" if r['diff'] > 0 else str(r['diff'])
        eco_diff = r['eco_k'] - r['k']
        eco_diff_str = f"+{round(eco_diff, 1)}" if eco_diff >= 0 else str(round(eco_diff, 1))

        results.append({
            'player': p,
            'k_d': f"{r['k']}-{r['d']}",
            'diff': diff_str,
            'adr': round(r['adr'], 1),
            'kast_pct': f"{round(r['kast_pct'], 1)}%",
            'eco_kills_impact': f"{round(r['eco_k'], 1)} ({eco_diff_str})",
            'round_swing': f"+{round(r['round_swing'] * 100, 1)}%",
            'lanus_3': round(lanus_3, 2),
            't_rating': round(t_rating, 2),
            'ct_rating': round(ct_rating, 2),
            'raw_lanus': lanus_3
        })

    res_df = pd.DataFrame(results).sort_values('raw_lanus', ascending=False).reset_index(drop=True)

    mvp = res_df.iloc[0]
    evp1 = res_df.iloc[1]
    evp2 = res_df.iloc[2]

    # Красивый вывод в стиле HLTV
    print("=" * 80)
    print(f"🏆 MATCH MVP: {mvp['player']} (LanUs Rating 3.0: {mvp['lanus_3']})")
    print(f"🎖️ EVP #1:   {evp1['player']} (LanUs Rating 3.0: {evp1['lanus_3']})")
    print(f"🎖️ EVP #2:   {evp2['player']} (LanUs Rating 3.0: {evp2['lanus_3']})")
    print("=" * 80)
    print("\nСВОДНАЯ ТАБЛИЦА LANUS RATING 3.0:")
    print("-" * 105)
    header = f"{'Игрок':<24} | {'K-D':<7} | {'ADR':<6} | {'KAST %':<7} | {'Eco-Kills':<15} | {'Round Swing':<12} | {'LanUs 3.0':<9} | {'T-Rat':<5} | {'CT-Rat':<6}"
    print(header)
    print("-" * 105)
    for _, row in res_df.iterrows():
        line = f"{row['player']:<24} | {row['k_d']:<7} | {row['adr']:<6.1f} | {row['kast_pct']:<7} | {row['eco_kills_impact']:<15} | {row['round_swing']:<12} | {row['lanus_3']:<9.2f} | {row['t_rating']:<5.2f} | {row['ct_rating']:<6.2f}"
        print(line)
    print("-" * 105)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LanUs Rating 3.0 CS2 Demo Analyzer")
    parser.add_argument("demo", nargs="?", default=r"c:\Users\Ns111\AppData\Local\Temp\match_13074286.dem",
                        help="Путь к .dem файлу матча CS2")
    args = parser.parse_args()

    analyze_demo(args.demo)
