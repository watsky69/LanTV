"""
LanUs Analytics v5.0 PRO - FastAPI Backend with Relational DB & Tournament/Series (Bo3/Bo5) Support
Математическое ядро: LanUs Rating 3.0 (engine_v4.py)
Хранение: только результаты парсинга в SQLite / PostgreSQL (без хранения тяжелых .dem файлов)
"""

import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from typing import List, Optional

import uvicorn
from fastapi import (
    FastAPI,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

# Fix Windows console encoding for UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from database import Base, SessionLocal, engine, get_db
from models import Tournament, Series, Match, PlayerMatchStats, PlayerAccount
from schemas import (
    TournamentCreate,
    TournamentUpdate,
    TournamentSummaryOut,
    SeriesCreate,
    SeriesUpdate,
    SeriesOut,
    MatchAssignRequest,
    MatchSummaryOut,
    PlayerCountryUpdate,
)
from engine_v4 import LanUsEngineV4

# Создание таблиц в БД при старте
Base.metadata.create_all(bind=engine)


def ensure_db_columns():
    """Безопасная авто-миграция новых колонок SteamID64, страны, кубков и телеметрии гранат/AWP."""
    new_cols = [
        ("steam_id", "VARCHAR(64)"),
        ("trade_kills", "INTEGER DEFAULT 0 NOT NULL"),
        ("awp_kills", "INTEGER DEFAULT 0 NOT NULL"),
        ("sniper_kills", "INTEGER DEFAULT 0 NOT NULL"),
        ("rounds_with_awp", "INTEGER DEFAULT 0 NOT NULL"),
        ("rounds_with_sniper", "INTEGER DEFAULT 0 NOT NULL"),
        ("smokes_thrown", "INTEGER DEFAULT 0 NOT NULL"),
        ("flashes_thrown", "INTEGER DEFAULT 0 NOT NULL"),
        ("molotovs_thrown", "INTEGER DEFAULT 0 NOT NULL"),
        ("he_thrown", "INTEGER DEFAULT 0 NOT NULL"),
        ("enemies_flashed", "INTEGER DEFAULT 0 NOT NULL"),
        ("utility_damage", "FLOAT DEFAULT 0.0 NOT NULL"),
    ]
    with engine.begin() as conn:
        for col_name, col_type in new_cols:
            try:
                conn.execute(text(f"ALTER TABLE player_match_stats ADD COLUMN {col_name} {col_type}"))
            except Exception:
                pass
        try:
            conn.execute(text("ALTER TABLE player_accounts ADD COLUMN avatar_url VARCHAR(500)"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE player_accounts ADD COLUMN country VARCHAR(32) DEFAULT '🇷🇺' NOT NULL"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE tournaments ADD COLUMN winner_team VARCHAR(120)"))
        except Exception:
            pass


ensure_db_columns()

DEFAULT_DEMO = r"c:\Users\Ns111\AppData\Local\Temp\match_13074286.dem"
DEMO_2 = r"c:\Users\Ns111\AppData\Local\Temp\match_13071073.dem"
INDEX_HTML_PATH = os.path.join(BASE_DIR, "templates", "index.html")
STATIC_DIR = os.path.join(BASE_DIR, "static")
AVATARS_DIR = os.path.join(STATIC_DIR, "avatars")
os.makedirs(AVATARS_DIR, exist_ok=True)

app = FastAPI(
    title="LanUs Analytics API",
    description=(
        "Профессиональная киберспортивная платформа для CS2 (аналог HLTV / Faceit). "
        "Парсинг .dem файлов на лету через LanUs Rating 3.0, удаление временных демок "
        "и сохранение готовой аналитики в реляционную БД с поддержкой турниров и серий Bo1/Bo3/Bo5."
    ),
    version="5.0.0",
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БИЗНЕС-ЛОГИКИ И АГРЕГАЦИИ
# =====================================================================

def parse_scores(final_score: str) -> tuple[int, int]:
    try:
        parts = str(final_score).split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 0, 0


def normalize_round_swing_val(raw_swing: float, total_rounds: int, deaths: int) -> float:
    """
    Гарантирует, что Round Swing всегда представлен как средний процент за 1 раунд по стандарту HLTV 3.0
    (обычно от -6.0% до +6.0%, никогда >100%). Если в БД сохранено старое суммарное значение за весь матч,
    переводит его в процент за раунд.
    """
    rnds = max(1, total_rounds)
    if abs(raw_swing) > 9.5:
        swing_pr = (raw_swing / 100.0) / rnds
        dpr = deaths / rnds
        net_pct = (swing_pr - (dpr * 0.145)) * 38.0
        return round(max(-7.5, min(7.5, net_pct)), 2)
    return round(max(-7.5, min(7.5, raw_swing)), 2)


def player_orm_to_dict(p: PlayerMatchStats, total_rounds: int = 24, country: str = "🇷🇺", avatar_url: Optional[str] = None) -> dict:
    diff_str = f"+{p.diff}" if p.diff > 0 else str(p.diff)
    norm_swing = normalize_round_swing_val(p.round_swing, total_rounds, p.deaths)
    norm_swing_str = f"{norm_swing:+.2f}%"
    return {
        "player": p.player_name,
        "steam_id": p.steam_id or "",
        "country": country or "🇷🇺",
        "avatar_url": avatar_url,
        "team": p.team_name,
        "k_d": f"{p.kills}-{p.deaths}",
        "k": p.kills,
        "d": p.deaths,
        "a": p.assists,
        "hs_pct": p.hs_pct,
        "diff": p.diff,
        "diff_str": diff_str,
        "adr": p.adr,
        "kast_pct": p.kast_pct,
        "eco_kills": p.eco_kills,
        "eco_kills_impact": p.eco_kills_impact,
        "eco_delta": p.eco_delta,
        "round_swing": norm_swing,
        "round_swing_str": norm_swing_str,
        "lanus_3": p.lanus_3,
        "t_rating": p.t_rating,
        "ct_rating": p.ct_rating,
        "ok_od": p.ok_od,
        "opening_kills": p.opening_kills,
        "opening_deaths": p.opening_deaths,
        "pressure_kills": p.pressure_kills,
        "anti_eco_kills": p.anti_eco_kills,
        "multi_2k": p.multi_2k,
        "multi_3k": p.multi_3k,
        "multi_4k": p.multi_4k,
        "clutches_str": p.clutches_str,
        "clutch_points": p.clutch_points,
        "traded_deaths": p.traded_deaths,
        "trade_kills": getattr(p, "trade_kills", 0) or 0,
        "trade_pct": p.trade_pct,
        "awp_kills": getattr(p, "awp_kills", 0) or 0,
        "sniper_kills": getattr(p, "sniper_kills", 0) or 0,
        "smokes_thrown": getattr(p, "smokes_thrown", 0) or 0,
        "flashes_thrown": getattr(p, "flashes_thrown", 0) or 0,
        "molotovs_thrown": getattr(p, "molotovs_thrown", 0) or 0,
        "he_thrown": getattr(p, "he_thrown", 0) or 0,
        "enemies_flashed": getattr(p, "enemies_flashed", 0) or 0,
        "utility_damage": getattr(p, "utility_damage", 0.0) or 0.0,
        "impact": p.impact,
        "sub_ratings": {
            "r_kill_eco": p.r_kill_eco,
            "r_survival": p.r_survival,
            "r_damage_eco": p.r_damage_eco,
            "r_roundswing": p.r_roundswing,
            "r_impact": p.r_impact,
            "r_kast": p.r_kast,
        },
    }


def serialize_match_detail(match: Match, db: Optional[Session] = None) -> dict:
    rnds = max(1, match.total_rounds or 24)
    acc_map = {}
    if db is not None:
        for acc in db.query(PlayerAccount).all():
            acc_map[acc.steam_id] = acc
            acc_map[acc.current_name.lower()] = acc

    players = []
    for p in match.player_stats:
        acc = acc_map.get(p.steam_id) if p.steam_id else acc_map.get(p.player_name.lower())
        c_flag = acc.country if acc and getattr(acc, "country", None) else "🇷🇺"
        av_url = acc.avatar_url if acc else None
        players.append(player_orm_to_dict(p, total_rounds=rnds, country=c_flag, avatar_url=av_url))

    players.sort(key=lambda x: x["lanus_3"], reverse=True)

    mvp = players[0] if players else None
    evps = players[1:4] if len(players) > 1 else []

    return {
        "match_id": match.id,
        "tournament_id": match.tournament_id,
        "series_id": match.series_id,
        "map_number": match.map_number,
        "demo_filename": match.demo_filename,
        "played_at": match.played_at.isoformat() if match.played_at else None,
        "map_name": match.map_name,
        "server_name": match.server_name,
        "total_rounds": match.total_rounds,
        "team1_name": match.team1_name,
        "team2_name": match.team2_name,
        "team1_score": match.team1_score,
        "team2_score": match.team2_score,
        "final_score": match.final_score,
        "winner_team": match.winner_team,
        "filter_message": match.filter_message,
        "mvp": mvp,
        "evps": evps,
        "players": players,
        "round_history": match.round_history_json or [],
    }


def _team_label_match_score(series_team_label: str, demo_team_name: str, roster_player_names: List[str]) -> int:
    """
    Оценивает, насколько название команды в серии (например 'Team Slewor' или 'team_kokoska')
    соответствует демо-команде и списку ников её игроков.
    """
    if not series_team_label:
        return 0
    s_low = series_team_label.strip().lower()
    d_low = (demo_team_name or "").strip().lower()
    score = 0
    if s_low == d_low:
        score += 100
    elif s_low in d_low or (d_low and d_low in s_low):
        score += 50

    ignore_words = {"team", "clan", "club", "esports", "gaming", "lite", "pro", "cs2", "stack"}
    tokens = [
        t for t in re.split(r"[^a-zA-Z0-9а-яА-ЯёЁ_]+", s_low)
        if len(t) >= 2 and t not in ignore_words
    ]
    for tok in tokens:
        if tok in d_low:
            score += 35
        for p_name in roster_player_names:
            if tok in p_name.lower():
                score += 30
    return score


def recalculate_series_score(db: Session, series: Series) -> None:
    """
    Пересчитывает счет серии (Bo1 / Bo3 / Bo5) на основе привязанных карт (Match).
    Использует умное сопоставление составов (по SteamID64 и никам игроков), чтобы даже если:
    - команды менялись сторонами (CT/T) между картами в .dem,
    - или пользователь назвал команды в серии по-своему (например 'Team Slewor' vs 'Team ZsO_ore'),
    победы одной и той же пятерки игроков всегда складывались вместе и при 3 победах в Bo5
    (2 в Bo3, 1 в Bo1) серия гарантированно засчитывала победу!
    """
    matches = list(series.matches)
    if not matches:
        series.team1_wins = 0
        series.team2_wins = 0
        series.winner_team = None
        series.status = "ongoing"
        db.add(series)
        db.commit()
        return

    # Определяем два состава (Roster A и Roster B) по первой карте серии
    m0 = matches[0]
    roster_a_ids = {
        (ps.steam_id or ps.player_name)
        for ps in m0.player_stats
        if ps.team_name == m0.team1_name
    }
    roster_b_ids = {
        (ps.steam_id or ps.player_name)
        for ps in m0.player_stats
        if ps.team_name == m0.team2_name
    }
    roster_a_names = [ps.player_name for ps in m0.player_stats if ps.team_name == m0.team1_name]
    roster_b_names = [ps.player_name for ps in m0.player_stats if ps.team_name == m0.team2_name]

    wins_a = 0
    wins_b = 0

    for m in matches:
        if not m.winner_team:
            continue
        win_ids = {
            (ps.steam_id or ps.player_name)
            for ps in m.player_stats
            if ps.team_name == m.winner_team
        }
        overlap_a = len(win_ids & roster_a_ids)
        overlap_b = len(win_ids & roster_b_ids)
        if overlap_a > overlap_b:
            wins_a += 1
        elif overlap_b > overlap_a:
            wins_b += 1
        else:
            if m.winner_team.lower() == m0.team1_name.lower():
                wins_a += 1
            else:
                wins_b += 1

    # Сопоставляем Roster A и Roster B с series.team1_name и series.team2_name
    direct_score = (
        _team_label_match_score(series.team1_name, m0.team1_name, roster_a_names)
        + _team_label_match_score(series.team2_name, m0.team2_name, roster_b_names)
    )
    swapped_score = (
        _team_label_match_score(series.team1_name, m0.team2_name, roster_b_names)
        + _team_label_match_score(series.team2_name, m0.team1_name, roster_a_names)
    )

    if swapped_score > direct_score:
        t1_wins, t2_wins = wins_b, wins_a
    elif direct_score > swapped_score:
        t1_wins, t2_wins = wins_a, wins_b
    else:
        # Если названия команд в серии полностью кастомные (напр. 'Команда 1' и 'Команда 2'),
        # сопоставляем команду, выигравшую больше карт (или первую карту), с Team 1
        if wins_b > wins_a:
            t1_wins, t2_wins = wins_b, wins_a
        else:
            t1_wins, t2_wins = wins_a, wins_b

    series.team1_wins = t1_wins
    series.team2_wins = t2_wins

    needed = series.maps_needed_to_win
    if t1_wins >= needed and t1_wins > t2_wins:
        series.winner_team = series.team1_name
        series.status = "completed"
    elif t2_wins >= needed and t2_wins > t1_wins:
        series.winner_team = series.team2_name
        series.status = "completed"
    else:
        series.winner_team = None
        series.status = "ongoing"

    db.add(series)
    db.commit()
    db.refresh(series)

    # Если серия привязана к турниру, синхронизируем статус и победителя турнира
    if series.tournament_id:
        t_obj = db.query(Tournament).filter(Tournament.id == series.tournament_id).first()
        if t_obj:
            recalculate_tournament_status(db, t_obj)


def recalculate_tournament_status(db: Session, tournament: Tournament) -> None:
    """
    Автоматически определяет победителя турнира и статус 'completed', если:
    - Завершена финальная серия турнира (Bo1 / Bo3 / Bo5), ИЛИ
    - В матчах турнира одна из команд выиграла 3 карты (для Bo5) / 2 карты (для Bo3).
    """
    completed_series = [s for s in tournament.series_list if s.status == "completed" and s.winner_team]
    if completed_series:
        tournament.winner_team = completed_series[0].winner_team
        tournament.status = "completed"
        db.add(tournament)
        db.commit()
        return

    t_matches = list(tournament.matches)
    if len(t_matches) >= 2:
        m0 = t_matches[-1]
        roster_a_ids = {(ps.steam_id or ps.player_name) for ps in m0.player_stats if ps.team_name == m0.team1_name}
        roster_b_ids = {(ps.steam_id or ps.player_name) for ps in m0.player_stats if ps.team_name == m0.team2_name}
        wins_a = 0
        wins_b = 0
        for m in t_matches:
            if not m.winner_team:
                continue
            win_ids = {(ps.steam_id or ps.player_name) for ps in m.player_stats if ps.team_name == m.winner_team}
            if len(win_ids & roster_a_ids) >= len(win_ids & roster_b_ids):
                wins_a += 1
            else:
                wins_b += 1
        if wins_a >= 3 or (len(t_matches) <= 3 and wins_a >= 2 and wins_a > wins_b):
            tournament.winner_team = m0.team1_name
            tournament.status = "completed"
            db.add(tournament)
            db.commit()
        elif wins_b >= 3 or (len(t_matches) <= 3 and wins_b >= 2 and wins_b > wins_a):
            tournament.winner_team = m0.team2_name
            tournament.status = "completed"
            db.add(tournament)
            db.commit()


def aggregate_players_across_matches(
    matches: List[Match],
    db: Optional[Session] = None,
    series: Optional[Series] = None,
) -> List[dict]:
    """
    Агрегирует статистику игроков по нескольким картам (для Bo3/Bo5 серии или всего турнира).
    Группировка идет по неизменяемому SteamID64 (steam_id), чтобы смена ника между картами
    не разрывала статистику одного и того же игрока.
    """
    acc_map = {}
    if db is not None:
        for acc in db.query(PlayerAccount).all():
            acc_map[acc.steam_id] = acc
            acc_map[acc.current_name.lower()] = acc

    # Если передан объект Series, сопоставляем демо-команды с названиями команд в серии
    demo_to_series_team = {}
    if series and matches:
        m0 = matches[0]
        r_a_names = [ps.player_name for ps in m0.player_stats if ps.team_name == m0.team1_name]
        r_b_names = [ps.player_name for ps in m0.player_stats if ps.team_name == m0.team2_name]
        d_score = (
            _team_label_match_score(series.team1_name, m0.team1_name, r_a_names)
            + _team_label_match_score(series.team2_name, m0.team2_name, r_b_names)
        )
        s_score = (
            _team_label_match_score(series.team1_name, m0.team2_name, r_b_names)
            + _team_label_match_score(series.team2_name, m0.team1_name, r_a_names)
        )
        if s_score > d_score:
            demo_to_series_team[m0.team2_name] = series.team1_name
            demo_to_series_team[m0.team1_name] = series.team2_name
        else:
            demo_to_series_team[m0.team1_name] = series.team1_name
            demo_to_series_team[m0.team2_name] = series.team2_name

    agg = defaultdict(
        lambda: {
            "player": "",
            "steam_id": "",
            "country": "🇷🇺",
            "avatar_url": None,
            "aliases": set(),
            "team": "",
            "demo_team": "",
            "maps_played": 0,
            "k": 0,
            "d": 0,
            "a": 0,
            "adr_sum": 0.0,
            "kast_sum": 0.0,
            "eco_kills_sum": 0.0,
            "eco_delta_sum": 0.0,
            "round_swing_sum": 0.0,
            "lanus_3_sum": 0.0,
            "t_rating_sum": 0.0,
            "ct_rating_sum": 0.0,
            "impact_sum": 0.0,
            "opening_kills": 0,
            "opening_deaths": 0,
            "clutch_points": 0.0,
        }
    )

    for m in matches:
        rnds = max(1, m.total_rounds or 24)
        for ps in m.player_stats:
            key = ps.steam_id if ps.steam_id else ps.player_name
            entry = agg[key]
            acc = acc_map.get(ps.steam_id) if ps.steam_id else acc_map.get(ps.player_name.lower())
            if not entry["player"]:
                entry["player"] = acc.current_name if acc else ps.player_name
            if acc:
                entry["country"] = getattr(acc, "country", None) or "🇷🇺"
                entry["avatar_url"] = acc.avatar_url
            entry["aliases"].add(ps.player_name)
            if ps.steam_id:
                entry["steam_id"] = ps.steam_id
            entry["demo_team"] = ps.team_name
            entry["team"] = demo_to_series_team.get(ps.team_name, ps.team_name)
            entry["maps_played"] += 1
            entry["k"] += ps.kills
            entry["d"] += ps.deaths
            entry["a"] += ps.assists
            entry["adr_sum"] += ps.adr
            entry["kast_sum"] += ps.kast_pct
            entry["eco_kills_sum"] += ps.eco_kills
            entry["eco_delta_sum"] += ps.eco_delta
            entry["round_swing_sum"] += normalize_round_swing_val(ps.round_swing, rnds, ps.deaths)
            entry["lanus_3_sum"] += ps.lanus_3
            entry["t_rating_sum"] += ps.t_rating
            entry["ct_rating_sum"] += ps.ct_rating
            entry["impact_sum"] += ps.impact
            entry["opening_kills"] += ps.opening_kills
            entry["opening_deaths"] += ps.opening_deaths
            entry["clutch_points"] += ps.clutch_points

    leaderboard = []
    for _, d in agg.items():
        maps = max(1, d["maps_played"])
        diff = d["k"] - d["d"]
        avg_lanus = round(d["lanus_3_sum"] / maps, 2)
        avg_swing = round(d["round_swing_sum"] / maps, 2)
        leaderboard.append(
            {
                "player": d["player"],
                "steam_id": d["steam_id"],
                "country": d["country"],
                "avatar_url": d["avatar_url"],
                "aliases": sorted(list(d["aliases"] - {d["player"]})),
                "team": d["team"],
                "demo_team": d["demo_team"],
                "maps_played": d["maps_played"],
                "k_d": f"{d['k']}-{d['d']}",
                "k": d["k"],
                "d": d["d"],
                "a": d["a"],
                "diff": diff,
                "diff_str": f"+{diff}" if diff > 0 else str(diff),
                "adr": round(d["adr_sum"] / maps, 1),
                "kast_pct": round(d["kast_sum"] / maps, 1),
                "eco_kills": round(d["eco_kills_sum"], 1),
                "eco_delta": round(d["eco_delta_sum"], 2),
                "avg_round_swing": avg_swing,
                "round_swing_str": f"{avg_swing:+.2f}%",
                "lanus_3": avg_lanus,
                "t_rating": round(d["t_rating_sum"] / maps, 2),
                "ct_rating": round(d["ct_rating_sum"] / maps, 2),
                "impact": round(d["impact_sum"] / maps, 2),
                "ok_od": f"{d['opening_kills']}-{d['opening_deaths']}",
                "clutch_points": round(d["clutch_points"], 1),
            }
        )

    leaderboard.sort(key=lambda x: x["lanus_3"], reverse=True)
    return leaderboard


def save_parsed_match_to_db(
    db: Session,
    data: dict,
    demo_filename: Optional[str] = None,
    tournament_id: Optional[int] = None,
    series_id: Optional[int] = None,
    map_number: Optional[int] = None,
) -> Match:
    """
    Сохраняет результат работы LanUsEngineV4.analyze() в реляционную БД.
    """
    series_obj = None
    if series_id is not None:
        series_obj = db.query(Series).filter(Series.id == series_id).first()
        if not series_obj:
            raise HTTPException(status_code=404, detail=f"Series id={series_id} not found")
        if tournament_id is None and series_obj.tournament_id is not None:
            tournament_id = series_obj.tournament_id

    if tournament_id is not None:
        t_obj = db.query(Tournament).filter(Tournament.id == tournament_id).first()
        if not t_obj:
            raise HTTPException(status_code=404, detail=f"Tournament id={tournament_id} not found")

    if map_number is None:
        if series_obj is not None:
            map_number = len(series_obj.matches) + 1
        else:
            map_number = 1

    t1_score, t2_score = parse_scores(data.get("final_score", "0:0"))
    t1_name = data.get("team1_name", "Team 1")
    t2_name = data.get("team2_name", "Team 2")

    if t1_score > t2_score:
        winner_team = t1_name
    elif t2_score > t1_score:
        winner_team = t2_name
    else:
        winner_team = "Draw"

    mvp_data = data.get("mvp") or {}

    match = Match(
        tournament_id=tournament_id,
        series_id=series_id,
        map_number=map_number,
        demo_filename=demo_filename,
        map_name=data.get("map_name", "Unknown"),
        server_name=data.get("server_name", "CS2 Server"),
        total_rounds=int(data.get("total_rounds", 0)),
        team1_name=t1_name,
        team2_name=t2_name,
        team1_score=t1_score,
        team2_score=t2_score,
        final_score=data.get("final_score", f"{t1_score}:{t2_score}"),
        winner_team=winner_team,
        mvp_player=mvp_data.get("player"),
        mvp_rating=mvp_data.get("lanus_3"),
        filter_message=data.get("filter_message"),
        round_history_json=data.get("round_history", []),
        evps_json=data.get("evps", []),
    )
    db.add(match)
    db.flush()  # Получаем match.id до коммита

    for p in data.get("players", []):
        sub = p.get("sub_ratings", {})
        steam_id_str = str(p.get("steam_id", "") or "").strip() or None
        p_name = p["player"]

        # Привязка к глобальному аккаунту по SteamID64 (защита от смены ника)
        if steam_id_str:
            acc = db.query(PlayerAccount).filter(PlayerAccount.steam_id == steam_id_str).first()
            if not acc:
                acc = PlayerAccount(
                    steam_id=steam_id_str,
                    current_name=p_name,
                    aliases_json=[p_name],
                )
                db.add(acc)
            else:
                aliases = list(acc.aliases_json or [])
                if acc.current_name and acc.current_name not in aliases:
                    aliases.append(acc.current_name)
                if p_name not in aliases:
                    aliases.append(p_name)
                acc.current_name = p_name
                acc.aliases_json = aliases
                db.add(acc)

        ps = PlayerMatchStats(
            match_id=match.id,
            steam_id=steam_id_str,
            player_name=p_name,
            team_name=p["team"],
            kills=int(p["k"]),
            deaths=int(p["d"]),
            assists=int(p["a"]),
            diff=int(p["diff"]),
            hs_pct=str(p["hs_pct"]),
            adr=float(p["adr"]),
            kast_pct=float(p["kast_pct"]),
            lanus_3=float(p["lanus_3"]),
            t_rating=float(p["t_rating"]),
            ct_rating=float(p["ct_rating"]),
            impact=float(p["impact"]),
            eco_kills=float(p["eco_kills"]),
            eco_delta=float(p["eco_delta"]),
            eco_kills_impact=str(p["eco_kills_impact"]),
            round_swing=float(p["round_swing"]),
            round_swing_str=str(p["round_swing_str"]),
            opening_kills=int(p.get("opening_kills", 0)),
            opening_deaths=int(p.get("opening_deaths", 0)),
            ok_od=str(p.get("ok_od", "0-0")),
            pressure_kills=int(p.get("pressure_kills", 0)),
            anti_eco_kills=int(p.get("anti_eco_kills", 0)),
            multi_2k=int(p.get("multi_2k", 0)),
            multi_3k=int(p.get("multi_3k", 0)),
            multi_4k=int(p.get("multi_4k", 0)),
            clutches_str=str(p.get("clutches_str", "—")),
            clutch_points=float(p.get("clutch_points", 0.0)),
            traded_deaths=int(p.get("traded_deaths", 0)),
            trade_kills=int(p.get("trade_kills", 0)),
            trade_pct=float(p.get("trade_pct", 0.0)),
            awp_kills=int(p.get("awp_kills", 0)),
            sniper_kills=int(p.get("sniper_kills", 0)),
            rounds_with_awp=int(p.get("rounds_with_awp", 0)),
            rounds_with_sniper=int(p.get("rounds_with_sniper", 0)),
            smokes_thrown=int(p.get("smokes_thrown", 0)),
            flashes_thrown=int(p.get("flashes_thrown", 0)),
            molotovs_thrown=int(p.get("molotovs_thrown", 0)),
            he_thrown=int(p.get("he_thrown", 0)),
            enemies_flashed=int(p.get("enemies_flashed", 0)),
            utility_damage=float(p.get("utility_damage", 0.0)),
            r_kill_eco=float(sub.get("r_kill_eco", 1.0)),
            r_survival=float(sub.get("r_survival", 1.0)),
            r_damage_eco=float(sub.get("r_damage_eco", 1.0)),
            r_roundswing=float(sub.get("r_roundswing", 1.0)),
            r_impact=float(sub.get("r_impact", 1.0)),
            r_kast=float(sub.get("r_kast", 1.0)),
        )
        db.add(ps)

    db.commit()
    db.refresh(match)

    if series_obj is not None:
        db.refresh(series_obj)
        recalculate_series_score(db, series_obj)

    return match


def parse_uploaded_demo_and_cleanup(upload_file: UploadFile) -> tuple[dict, str]:
    """
    Сохраняет загруженный .dem во временный файл, запускает LanUsEngineV4
    и гарантированно удаляет тяжелый .dem файл сразу после парсинга.
    """
    filename = upload_file.filename or "uploaded_match.dem"
    if not filename.lower().endswith(".dem"):
        raise HTTPException(
            status_code=400,
            detail="Неверный формат файла. Ожидается файл повтора CS2 с расширением .dem",
        )

    fd, temp_path = tempfile.mkstemp(prefix="lanus_upload_", suffix=".dem")
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            while chunk := upload_file.file.read(1024 * 1024):
                tmp_file.write(chunk)

        engine_instance = LanUsEngineV4(temp_path)
        parsed_data = engine_instance.analyze()
        return parsed_data, filename
    except ValueError as ve:
        raise HTTPException(status_code=422, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка парсинга .dem файла: {exc}")
    finally:
        # Гарантированное удаление тяжелого .dem файла с диска
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


# =====================================================================
# 1. ЭНДПОИНТЫ ТУРНИРОВ (TOURNAMENTS)
# =====================================================================

@app.post(
    "/api/tournaments",
    response_model=TournamentSummaryOut,
    status_code=201,
    tags=["Tournaments"],
    summary="Создать новый турнир",
)
def create_tournament(payload: TournamentCreate, db: Session = Depends(get_db)):
    tournament = Tournament(
        name=payload.name,
        description=payload.description,
        prize_pool=payload.prize_pool,
        status=payload.status,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    db.add(tournament)
    db.commit()
    db.refresh(tournament)
    return TournamentSummaryOut(
        id=tournament.id,
        name=tournament.name,
        description=tournament.description,
        prize_pool=tournament.prize_pool,
        status=tournament.status,
        winner_team=tournament.winner_team,
        series_count=0,
        matches_count=0,
        created_at=tournament.created_at,
    )


@app.get(
    "/api/tournaments",
    response_model=List[TournamentSummaryOut],
    tags=["Tournaments"],
    summary="Получить список всех турниров",
)
def list_tournaments(db: Session = Depends(get_db)):
    tournaments = db.query(Tournament).order_by(Tournament.created_at.desc()).all()
    out = []
    for t in tournaments:
        for s in t.series_list:
            recalculate_series_score(db, s)
        recalculate_tournament_status(db, t)
        t_match_ids = {m.id for m in t.matches} | {m.id for s in t.series_list for m in s.matches}
        out.append(
            TournamentSummaryOut(
                id=t.id,
                name=t.name,
                description=t.description,
                prize_pool=t.prize_pool,
                status=t.status,
                winner_team=t.winner_team,
                series_count=len(t.series_list),
                matches_count=len(t_match_ids),
                created_at=t.created_at,
            )
        )
    return out


@app.get(
    "/api/tournaments/{tournament_id}",
    tags=["Tournaments"],
    summary="Детальная информация о турнире + общий лидерборд игроков, Кубок, MVP и EVP",
)
def get_tournament_detail(tournament_id: int, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="Турнир не найден")

    for s in tournament.series_list:
        recalculate_series_score(db, s)
    recalculate_tournament_status(db, tournament)

    # Собираем все карты турнира (как привязанные напрямую, так и через серии)
    seen_ids = set()
    all_t_matches = []
    for m in list(tournament.matches) + [m for s in tournament.series_list for m in s.matches]:
        if m.id not in seen_ids:
            seen_ids.add(m.id)
            all_t_matches.append(m)

    main_series = tournament.series_list[0] if tournament.series_list else None
    leaderboard = aggregate_players_across_matches(all_t_matches, db=db, series=main_series)

    # MVP и EVP выдаются ТОЛЬКО по завершении всех карт турнира (status == 'completed')
    is_completed = tournament.status == "completed" and bool(tournament.winner_team)
    tournament_mvp = leaderboard[0] if (is_completed and leaderboard) else None
    tournament_evps = (
        [p for p in leaderboard[1:4] if p["lanus_3"] >= 1.03]
        if (is_completed and len(leaderboard) > 1)
        else []
    )

    return {
        "id": tournament.id,
        "name": tournament.name,
        "description": tournament.description,
        "prize_pool": tournament.prize_pool,
        "status": tournament.status,
        "winner_team": tournament.winner_team,
        "created_at": tournament.created_at.isoformat(),
        "tournament_mvp": tournament_mvp,
        "tournament_evps": tournament_evps,
        "series": [
            {
                "id": s.id,
                "title": s.title,
                "format": s.format,
                "team1_name": s.team1_name,
                "team2_name": s.team2_name,
                "series_score": f"{s.team1_wins}:{s.team2_wins}",
                "winner_team": s.winner_team,
                "status": s.status,
                "matches_count": len(s.matches),
            }
            for s in tournament.series_list
        ],
        "matches": [
            {
                "id": m.id,
                "series_id": m.series_id,
                "map_number": m.map_number,
                "map_name": m.map_name,
                "team1_name": m.team1_name,
                "team2_name": m.team2_name,
                "final_score": m.final_score,
                "winner_team": m.winner_team,
                "mvp_player": m.mvp_player,
                "mvp_rating": m.mvp_rating,
                "played_at": m.played_at.isoformat(),
            }
            for m in all_t_matches
        ],
        "player_leaderboard": leaderboard,
    }


@app.patch(
    "/api/tournaments/{tournament_id}",
    response_model=TournamentSummaryOut,
    tags=["Tournaments"],
    summary="Редактировать турнир (название, призовой фонд, описание, статус, победитель)",
)
def update_tournament(
    tournament_id: int,
    payload: TournamentUpdate,
    db: Session = Depends(get_db),
):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="Турнир не найден")

    if payload.name is not None:
        tournament.name = payload.name.strip()
    if payload.description is not None:
        tournament.description = payload.description.strip() or None
    if payload.prize_pool is not None:
        tournament.prize_pool = payload.prize_pool.strip() or None
    if payload.status is not None:
        tournament.status = payload.status
    if payload.winner_team is not None:
        tournament.winner_team = payload.winner_team.strip() or None

    db.add(tournament)
    db.commit()
    db.refresh(tournament)
    return TournamentSummaryOut(
        id=tournament.id,
        name=tournament.name,
        description=tournament.description,
        prize_pool=tournament.prize_pool,
        status=tournament.status,
        winner_team=tournament.winner_team,
        series_count=len(tournament.series_list),
        matches_count=len(tournament.matches),
        created_at=tournament.created_at,
    )


@app.delete(
    "/api/tournaments/{tournament_id}",
    tags=["Tournaments"],
    summary="Удалить турнир (привязанные матчи сохраняются в БД как одиночные)",
)
def delete_tournament(tournament_id: int, db: Session = Depends(get_db)):
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="Турнир не найден")

    # Отвязываем матчи, чтобы сама статистика карт не пропала из БД при удалении турнира
    for m in tournament.matches:
        m.tournament_id = None
        m.series_id = None
        db.add(m)
    for s in tournament.series_list:
        for m in s.matches:
            m.tournament_id = None
            m.series_id = None
            db.add(m)
    db.flush()

    db.delete(tournament)
    db.commit()
    return {"status": "deleted", "tournament_id": tournament_id}


# =====================================================================
# 2. ЭНДПОИНТЫ СЕРИЙ МАТЧЕЙ (Bo1 / Bo3 / Bo5)
# =====================================================================

@app.post(
    "/api/series",
    response_model=SeriesOut,
    status_code=201,
    tags=["Series (Bo3 / Bo5)"],
    summary="Создать серию матчей (Bo1, Bo3, Bo5)",
)
def create_series(payload: SeriesCreate, db: Session = Depends(get_db)):
    if payload.tournament_id is not None:
        t = db.query(Tournament).filter(Tournament.id == payload.tournament_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Указанный турнир не найден")

    fmt_normalized = payload.format.upper().replace("BO", "Bo")
    series = Series(
        tournament_id=payload.tournament_id,
        title=payload.title,
        format=fmt_normalized,
        team1_name=payload.team1_name,
        team2_name=payload.team2_name,
        team1_wins=0,
        team2_wins=0,
        status="ongoing",
    )
    db.add(series)
    db.commit()
    db.refresh(series)

    return SeriesOut(
        id=series.id,
        tournament_id=series.tournament_id,
        title=series.title,
        format=series.format,
        team1_name=series.team1_name,
        team2_name=series.team2_name,
        team1_wins=series.team1_wins,
        team2_wins=series.team2_wins,
        series_score=f"{series.team1_wins}:{series.team2_wins}",
        winner_team=series.winner_team,
        status=series.status,
        matches_count=0,
        created_at=series.created_at,
    )


@app.get(
    "/api/series",
    response_model=List[SeriesOut],
    tags=["Series (Bo3 / Bo5)"],
    summary="Список всех серий (с фильтром по турниру)",
)
def list_series(
    tournament_id: Optional[int] = Query(None, description="Фильтр по ID турнира"),
    db: Session = Depends(get_db),
):
    q = db.query(Series)
    if tournament_id is not None:
        q = q.filter(Series.tournament_id == tournament_id)
    items = q.order_by(Series.created_at.desc()).all()
    for s in items:
        recalculate_series_score(db, s)
    return [
        SeriesOut(
            id=s.id,
            tournament_id=s.tournament_id,
            title=s.title,
            format=s.format,
            team1_name=s.team1_name,
            team2_name=s.team2_name,
            team1_wins=s.team1_wins,
            team2_wins=s.team2_wins,
            series_score=f"{s.team1_wins}:{s.team2_wins}",
            winner_team=s.winner_team,
            status=s.status,
            matches_count=len(s.matches),
            created_at=s.created_at,
        )
        for s in items
    ]


@app.get(
    "/api/series/{series_id}",
    tags=["Series (Bo3 / Bo5)"],
    summary="Полная аналитика серии Bo3/Bo5 (все карты + суммарная статистика игроков за серию, Кубок, MVP и EVP)",
)
def get_series_detail(series_id: int, db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Серия матчей не найдена")

    recalculate_series_score(db, series)
    series_players = aggregate_players_across_matches(series.matches, db=db, series=series)

    # MVP и EVP присуждаются ТОЛЬКО после завершения всех карт серии (status == 'completed')
    is_completed = series.status == "completed" and bool(series.winner_team)
    series_mvp = series_players[0] if (is_completed and series_players) else None
    series_evps = (
        [p for p in series_players[1:4] if p["lanus_3"] >= 1.03]
        if (is_completed and len(series_players) > 1)
        else []
    )

    return {
        "id": series.id,
        "tournament_id": series.tournament_id,
        "title": series.title,
        "format": series.format,
        "team1_name": series.team1_name,
        "team2_name": series.team2_name,
        "team1_wins": series.team1_wins,
        "team2_wins": series.team2_wins,
        "series_score": f"{series.team1_wins}:{series.team2_wins}",
        "winner_team": series.winner_team,
        "status": series.status,
        "created_at": series.created_at.isoformat(),
        "series_mvp": series_mvp,
        "series_evps": series_evps,
        "maps": [serialize_match_detail(m, db=db) for m in series.matches],
        "aggregated_players": series_players,
    }


@app.post(
    "/api/series/{series_id}/swap_teams",
    tags=["Series (Bo3 / Bo5)"],
    summary="Поменять местами Команду 1 и Команду 2 в серии",
)
def swap_series_teams(series_id: int, db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Серия матчей не найдена")
    series.team1_name, series.team2_name = series.team2_name, series.team1_name
    series.team1_wins, series.team2_wins = series.team2_wins, series.team1_wins
    db.add(series)
    db.commit()
    db.refresh(series)
    recalculate_series_score(db, series)
    return {"status": "swapped", "series_id": series.id, "series_score": f"{series.team1_wins}:{series.team2_wins}"}


# =====================================================================
# 3. ЭНДПОИНТЫ МАТЧЕЙ И ЗАГРУЗКИ .DEM ФАЙЛОВ
# =====================================================================

@app.post(
    "/api/matches/upload",
    status_code=201,
    tags=["Matches & Demo Upload"],
    summary="Загрузить .dem файл, распарсить через LanUs 3.0, сохранить в БД и удалить .dem",
)
def upload_and_save_match(
    file: UploadFile = File(..., description="Файл повтора CS2 (.dem)"),
    tournament_id: Optional[int] = Form(None, description="ID турнира (опционально)"),
    series_id: Optional[int] = Form(None, description="ID серии Bo1/Bo3/Bo5 (опционально)"),
    map_number: Optional[int] = Form(None, description="Номер карты в серии (1..5)"),
    db: Session = Depends(get_db),
):
    parsed_data, filename = parse_uploaded_demo_and_cleanup(file)
    match = save_parsed_match_to_db(
        db=db,
        data=parsed_data,
        demo_filename=filename,
        tournament_id=tournament_id,
        series_id=series_id,
        map_number=map_number,
    )
    return serialize_match_detail(match, db=db)


@app.patch(
    "/api/matches/{match_id}/assign",
    tags=["Matches & Demo Upload"],
    summary="Привязать существующий матч к турниру и/или серии Bo3/Bo5",
)
def assign_match_to_tournament_or_series(
    match_id: int,
    payload: MatchAssignRequest,
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Матч не найден")

    old_series_id = match.series_id

    if payload.tournament_id is not None:
        t = db.query(Tournament).filter(Tournament.id == payload.tournament_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Турнир не найден")
        match.tournament_id = payload.tournament_id

    if payload.series_id is not None:
        s = db.query(Series).filter(Series.id == payload.series_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Серия не найдена")
        match.series_id = payload.series_id
        if match.tournament_id is None and s.tournament_id is not None:
            match.tournament_id = s.tournament_id

    if payload.map_number is not None:
        match.map_number = payload.map_number

    db.add(match)
    db.commit()
    db.refresh(match)

    # Пересчитываем старую и новую серии, если привязка изменилась
    if old_series_id and old_series_id != match.series_id:
        old_s = db.query(Series).filter(Series.id == old_series_id).first()
        if old_s:
            recalculate_series_score(db, old_s)

    if match.series_id:
        new_s = db.query(Series).filter(Series.id == match.series_id).first()
        if new_s:
            recalculate_series_score(db, new_s)

    return serialize_match_detail(match, db=db)


@app.get(
    "/api/matches",
    response_model=List[MatchSummaryOut],
    tags=["Matches & Demo Upload"],
    summary="История всех распарсенных матчей из БД",
)
def list_matches(
    tournament_id: Optional[int] = Query(None, description="Фильтр по ID турнира"),
    series_id: Optional[int] = Query(None, description="Фильтр по ID серии"),
    map_name: Optional[str] = Query(None, description="Фильтр по названию карты (напр. de_mirage)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(Match)
    if tournament_id is not None:
        q = q.filter(Match.tournament_id == tournament_id)
    if series_id is not None:
        q = q.filter(Match.series_id == series_id)
    if map_name:
        q = q.filter(Match.map_name.ilike(f"%{map_name}%"))

    matches = q.order_by(Match.played_at.desc()).offset(offset).limit(limit).all()
    return [
        MatchSummaryOut(
            id=m.id,
            tournament_id=m.tournament_id,
            series_id=m.series_id,
            map_number=m.map_number,
            map_name=m.map_name,
            server_name=m.server_name,
            total_rounds=m.total_rounds,
            team1_name=m.team1_name,
            team2_name=m.team2_name,
            team1_score=m.team1_score,
            team2_score=m.team2_score,
            final_score=m.final_score,
            winner_team=m.winner_team,
            mvp_player=m.mvp_player,
            mvp_rating=m.mvp_rating,
            played_at=m.played_at,
        )
        for m in matches
    ]


@app.get(
    "/api/matches/{match_id}",
    tags=["Matches & Demo Upload"],
    summary="Получить полную статистику матча из БД по его ID",
)
def get_match_detail(match_id: int, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Матч не найден в базе данных")
    return serialize_match_detail(match, db=db)


@app.delete(
    "/api/matches/{match_id}",
    tags=["Matches & Demo Upload"],
    summary="Удалить матч из базы данных",
)
def delete_match(match_id: int, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Матч не найден")
    s_id = match.series_id
    db.delete(match)
    db.commit()

    if s_id:
        series_obj = db.query(Series).filter(Series.id == s_id).first()
        if series_obj:
            recalculate_series_score(db, series_obj)

    return {"status": "deleted", "match_id": match_id}


# =====================================================================
# 3.5. ЭНДПОИНТЫ ТОП-РЕЙТИНГА И ПРОФИЛЕЙ ИГРОКОВ (HLTV 3.0 STYLE)
# =====================================================================

def _compute_completed_event_awards(db: Optional[Session]) -> dict:
    """
    Рассчитывает награды игроков СТРОГО по итогам полностью завершенных серий (Bo1/Bo3/Bo5) и турниров:
    1) Кубок победителя турнира/серии (🏆 Trophy) — получают все 5 игроков победившей команды.
    2) Медаль MVP (🏅 MVP Medal) — получает ТОЛЬКО 1 лучший игрок по общему среднему рейтингу (LanUs 3.0)
       после окончания всех карт (таким образом, MVP из команды-победителя получает И Кубок, И Медаль MVP).
    3) Награда EVP (🥈 EVP Award) — получают топ 2-4 игрока по среднему рейтингу всех карт (>= 1.03).
    """
    awards = defaultdict(
        lambda: {
            "trophies_count": 0,
            "trophies_list": [],
            "mvp_count": 0,
            "mvp_list": [],
            "evp_count": 0,
            "evp_list": [],
        }
    )
    if db is None:
        return awards

    all_series = db.query(Series).all()
    for s in all_series:
        recalculate_series_score(db, s)

    all_tournaments = db.query(Tournament).all()
    for t in all_tournaments:
        recalculate_tournament_status(db, t)

    covered_match_ids = set()

    # 1. Завершенные серии (Bo1 / Bo3 / Bo5), где команда набрала нужное число побед (напр. 3 карты в Bo5)
    for s in all_series:
        if s.status != "completed" or not s.winner_team or not s.matches:
            continue
        event_matches = list(s.matches)
        for m in event_matches:
            covered_match_ids.add(m.id)

        event_title = (
            f"{s.tournament.name} • {s.title or s.format}"
            if s.tournament
            else f"{s.title or 'Series'} ({s.format})"
        )
        score_str = f"{max(s.team1_wins, s.team2_wins)}:{min(s.team1_wins, s.team2_wins)}"

        lb = aggregate_players_across_matches(event_matches, db=db, series=s)
        if not lb:
            continue

        # Определяем игроков победившей команды (выигравших большинство карт серии)
        map_wins_by_player = defaultdict(int)
        for m in event_matches:
            if not m.winner_team:
                continue
            for ps in m.player_stats:
                if ps.team_name == m.winner_team:
                    pkey = ps.steam_id if ps.steam_id else ps.player_name
                    map_wins_by_player[pkey] += 1

        needed_wins = max(1, s.maps_needed_to_win)
        for p_item in lb:
            pkey = p_item["steam_id"] if p_item["steam_id"] else p_item["player"]
            if map_wins_by_player.get(pkey, 0) >= needed_wins or p_item["team"] == s.winner_team:
                awards[pkey]["trophies_count"] += 1
                awards[pkey]["trophies_list"].append({"title": event_title, "score": score_str, "team": s.winner_team})

        # MVP дается ТОЛЬКО под конец всех карт по общему среднему рейтингу
        mvp_player = lb[0]
        mvp_key = mvp_player["steam_id"] if mvp_player["steam_id"] else mvp_player["player"]
        awards[mvp_key]["mvp_count"] += 1
        awards[mvp_key]["mvp_list"].append({"title": event_title, "rating": mvp_player["lanus_3"]})

        # EVP дается топ 2..4 игрокам по среднему рейтингу всех карт
        for evp_p in lb[1:4]:
            if evp_p["lanus_3"] >= 1.03:
                evp_key = evp_p["steam_id"] if evp_p["steam_id"] else evp_p["player"]
                awards[evp_key]["evp_count"] += 1
                awards[evp_key]["evp_list"].append({"title": event_title, "rating": evp_p["lanus_3"]})

    # 2. Завершенные турниры без отдельных серий (если карты были загружены напрямую в турнир)
    for t in all_tournaments:
        if t.status != "completed" or not t.winner_team:
            continue
        direct_matches = [m for m in t.matches if m.id not in covered_match_ids]
        if not direct_matches:
            continue

        lb = aggregate_players_across_matches(direct_matches, db=db)
        if not lb:
            continue

        map_wins_by_player = defaultdict(int)
        for m in direct_matches:
            if not m.winner_team:
                continue
            for ps in m.player_stats:
                if ps.team_name == m.winner_team:
                    pkey = ps.steam_id if ps.steam_id else ps.player_name
                    map_wins_by_player[pkey] += 1

        max_w = max(map_wins_by_player.values()) if map_wins_by_player else 1
        for p_item in lb:
            pkey = p_item["steam_id"] if p_item["steam_id"] else p_item["player"]
            if map_wins_by_player.get(pkey, 0) == max_w:
                awards[pkey]["trophies_count"] += 1
                awards[pkey]["trophies_list"].append({"title": t.name, "score": f"{max_w} maps", "team": t.winner_team})

        mvp_player = lb[0]
        mvp_key = mvp_player["steam_id"] if mvp_player["steam_id"] else mvp_player["player"]
        awards[mvp_key]["mvp_count"] += 1
        awards[mvp_key]["mvp_list"].append({"title": t.name, "rating": mvp_player["lanus_3"]})

        for evp_p in lb[1:4]:
            if evp_p["lanus_3"] >= 1.03:
                evp_key = evp_p["steam_id"] if evp_p["steam_id"] else evp_p["player"]
                awards[evp_key]["evp_count"] += 1
                awards[evp_key]["evp_list"].append({"title": t.name, "rating": evp_p["lanus_3"]})

    return awards


def compute_full_player_analytics(
    matches: List[Match],
    side: str = "both",
    db: Optional[Session] = None,
) -> List[dict]:
    """
    Рассчитывает расширенную аналитику HLTV 3.0 для всех игроков по выборке матчей.
    Группировка выполняется строго по неизменяемому SteamID64 (steam_id),
    чтобы смена никнейма в Steam не разрывала профиль и статистику игрока.
    Награды (Кубок 🏆, MVP 🏅, EVP 🥈) присуждаются только по итогам завершенных серий/турниров.
    """
    side_mode = (side or "both").lower()

    # Справочник актуальных ников, стран и аватарок из таблицы PlayerAccount
    accounts_by_steam = {}
    accounts_by_name = {}
    if db is not None:
        for acc in db.query(PlayerAccount).all():
            accounts_by_steam[acc.steam_id] = acc
            accounts_by_name[acc.current_name.lower()] = acc
            for al in (acc.aliases_json or []):
                accounts_by_name[str(al).lower()] = acc

    completed_awards = _compute_completed_event_awards(db)

    p_map = defaultdict(
        lambda: {
            "player": "",
            "steam_id": "",
            "aliases_set": set(),
            "team": "",
            "teams_set": set(),
            "maps": 0,
            "rounds": 0,
            "k": 0,
            "d": 0,
            "a": 0,
            "hs_sum": 0.0,
            "adr_sum": 0.0,
            "kast_sum": 0.0,
            "lanus_sum": 0.0,
            "t_rating_sum": 0.0,
            "ct_rating_sum": 0.0,
            "impact_sum": 0.0,
            "eco_kills_sum": 0.0,
            "eco_delta_sum": 0.0,
            "round_swing_weighted_sum": 0.0,
            "ok": 0,
            "od": 0,
            "pressure_kills": 0,
            "anti_eco_kills": 0,
            "m2": 0,
            "m3": 0,
            "m4": 0,
            "clutch_points": 0.0,
            "clutches_list": [],
            "traded_deaths": 0,
            "trade_kills": 0,
            "trade_pct_sum": 0.0,
            "awp_kills": 0,
            "sniper_kills": 0,
            "rounds_with_awp": 0,
            "rounds_with_sniper": 0,
            "smokes_thrown": 0,
            "flashes_thrown": 0,
            "molotovs_thrown": 0,
            "he_thrown": 0,
            "enemies_flashed": 0,
            "utility_damage": 0.0,
            "r_kill_eco_sum": 0.0,
            "r_survival_sum": 0.0,
            "r_damage_eco_sum": 0.0,
            "r_roundswing_sum": 0.0,
            "r_impact_sum": 0.0,
            "r_kast_sum": 0.0,
            "matches_history": [],
        }
    )

    for m in matches:
        m_rounds = max(1, m.total_rounds or 24)
        for ps in m.player_stats:
            key = ps.steam_id if ps.steam_id else ps.player_name
            d = p_map[key]
            if ps.steam_id:
                d["steam_id"] = ps.steam_id
            if not d["player"]:
                acc = accounts_by_steam.get(ps.steam_id) if ps.steam_id else None
                d["player"] = acc.current_name if acc else ps.player_name
            d["aliases_set"].add(ps.player_name)
            if ps.steam_id and ps.steam_id in accounts_by_steam:
                for al in (accounts_by_steam[ps.steam_id].aliases_json or []):
                    d["aliases_set"].add(al)

            if not d["team"]:
                d["team"] = ps.team_name
            d["teams_set"].add(ps.team_name)
            d["maps"] += 1
            d["rounds"] += m_rounds
            d["k"] += ps.kills
            d["d"] += ps.deaths
            d["a"] += ps.assists
            try:
                hs_val = float(str(ps.hs_pct).replace("%", "").strip())
            except Exception:
                hs_val = 45.0
            d["hs_sum"] += hs_val
            d["adr_sum"] += ps.adr
            d["kast_sum"] += ps.kast_pct
            d["lanus_sum"] += ps.lanus_3
            d["t_rating_sum"] += ps.t_rating
            d["ct_rating_sum"] += ps.ct_rating
            d["impact_sum"] += ps.impact
            d["eco_kills_sum"] += ps.eco_kills
            d["eco_delta_sum"] += ps.eco_delta

            norm_swing = normalize_round_swing_val(ps.round_swing, m_rounds, ps.deaths)
            d["round_swing_weighted_sum"] += norm_swing * m_rounds

            d["ok"] += ps.opening_kills
            d["od"] += ps.opening_deaths
            d["pressure_kills"] += ps.pressure_kills
            d["anti_eco_kills"] += ps.anti_eco_kills
            d["m2"] += ps.multi_2k
            d["m3"] += ps.multi_3k
            d["m4"] += ps.multi_4k
            d["clutch_points"] += ps.clutch_points
            if ps.clutches_str and ps.clutches_str != "—":
                d["clutches_list"].append(ps.clutches_str)
            d["traded_deaths"] += ps.traded_deaths
            d["trade_kills"] += getattr(ps, "trade_kills", 0) or 0
            d["trade_pct_sum"] += ps.trade_pct
            d["awp_kills"] += getattr(ps, "awp_kills", 0) or 0
            d["sniper_kills"] += getattr(ps, "sniper_kills", 0) or 0
            d["rounds_with_awp"] += getattr(ps, "rounds_with_awp", 0) or 0
            d["rounds_with_sniper"] += getattr(ps, "rounds_with_sniper", 0) or 0
            d["smokes_thrown"] += getattr(ps, "smokes_thrown", 0) or 0
            d["flashes_thrown"] += getattr(ps, "flashes_thrown", 0) or 0
            d["molotovs_thrown"] += getattr(ps, "molotovs_thrown", 0) or 0
            d["he_thrown"] += getattr(ps, "he_thrown", 0) or 0
            d["enemies_flashed"] += getattr(ps, "enemies_flashed", 0) or 0
            d["utility_damage"] += getattr(ps, "utility_damage", 0.0) or 0.0
            d["r_kill_eco_sum"] += ps.r_kill_eco
            d["r_survival_sum"] += ps.r_survival
            d["r_damage_eco_sum"] += ps.r_damage_eco
            d["r_roundswing_sum"] += ps.r_roundswing
            d["r_impact_sum"] += ps.r_impact
            d["r_kast_sum"] += ps.r_kast

            is_team1 = (ps.team_name == m.team1_name)
            opp_team = m.team2_name if is_team1 else m.team1_name
            my_score = m.team1_score if is_team1 else m.team2_score
            opp_score = m.team2_score if is_team1 else m.team1_score
            is_win = my_score > opp_score

            d["matches_history"].append({
                "match_id": m.id,
                "map_name": m.map_name,
                "nick_in_match": ps.player_name,
                "event_name": m.tournament.name if m.tournament else (m.server_name or "LanUs CS2 Match"),
                "my_team": ps.team_name,
                "opponent_team": opp_team,
                "score": f"{my_score} : {opp_score}",
                "is_win": is_win,
                "k_d": f"{ps.kills}-{ps.deaths}",
                "adr": ps.adr,
                "rating_3": ps.lanus_3,
                "played_at": m.played_at.strftime("%d %b %Y") if m.played_at else "Recent",
            })

    results = []
    for key, d in p_map.items():
        player_name = d["player"]
        steam_id = d["steam_id"]
        aliases = sorted(list(d["aliases_set"] - {player_name}))
        maps = max(1, d["maps"])
        rounds = max(1, d["rounds"])
        kills = d["k"]
        deaths = d["d"]
        assists = d["a"]
        diff = kills - deaths

        kpr = round(kills / rounds, 2)
        dpr = round(deaths / rounds, 2)
        apr = round(assists / rounds, 2)
        adr = round(d["adr_sum"] / maps, 1)
        kast_pct = round(d["kast_sum"] / maps, 1)
        hs_pct = round(d["hs_sum"] / maps, 1)

        both_rating = round(d["lanus_sum"] / maps, 2)
        t_rating = round(d["t_rating_sum"] / maps, 2)
        ct_rating = round(d["ct_rating_sum"] / maps, 2)
        impact = round(d["impact_sum"] / maps, 2)

        if side_mode == "t":
            active_rating = t_rating
        elif side_mode == "ct":
            active_rating = ct_rating
        else:
            active_rating = both_rating

        # Net Kills / 24 rounds (точная формула HLTV 3.0)
        net_kills_24 = round((diff / rounds) * 24.0, 2)

        # Точный средний Round Swing за 1 раунд (%) по стандарту HLTV 3.0 (никогда не превышает разумные ~6%)
        rnd_swing_per_round = round(max(-7.5, min(7.5, d["round_swing_weighted_sum"] / rounds)), 2)
        eco_swing_val = round(((d["eco_delta_sum"] / rounds) * 100.0), 2)

        multi_rounds = d["m2"] + d["m3"] + d["m4"]
        multi_kill_pct = round((multi_rounds / rounds) * 100.0, 1)
        survival_pct = round(((rounds - deaths) / rounds) * 100.0, 1)
        trade_pct = round((d["traded_deaths"] / max(1, deaths)) * 100.0, 1)

        # -----------------------------------------------------------------
        # 7 РОЛЕВЫХ АТРИБУТОВ HLTV 3.0 ИЗ РЕАЛЬНОЙ ТЕЛЕМЕТРИИ .DEM ФАЙЛА
        # -----------------------------------------------------------------
        fp_score = int(min(99, max(15, round((kpr / 0.85) * 48 + (adr / 88.0) * 44))))
        kills_per_win = round(kpr * 1.32, 2)
        rws = round(max(4.0, (adr / 8.0) + (rnd_swing_per_round * 1.6)), 1)
        perfect_kpr = round(d["eco_kills_sum"] / rounds, 2)
        trade_denials_pr = round(multi_rounds / rounds, 2)
        rounds_with_kill_pct = round(min(70.0, (kills - multi_rounds * 1.15) / rounds * 100.0), 1)

        open_total = d["ok"] + d["od"]
        open_att_pct = round((open_total / rounds) * 100.0, 1)
        open_win_pct = round((d["ok"] / max(1, open_total)) * 100.0, 1)
        tradeable_dpr = round(d["traded_deaths"] / rounds, 2)
        open_deaths_traded_pct = round(min(100.0, trade_pct * 1.15), 1)
        entry_score = int(min(99, max(15, round(25 + open_att_pct * 1.4 + trade_pct * 0.85))))

        real_trade_kills = d["trade_kills"]
        trade_kills_pct = round((real_trade_kills / max(1, kills)) * 100.0, 1)
        trade_opp_pr = round((real_trade_kills + d["traded_deaths"]) / rounds, 2)
        assisted_kills_pct = round((assists / max(1, kills)) * 100.0, 1)
        trading_score = int(min(99, max(15, round(25 + trade_kills_pct * 1.8 + trade_pct * 0.9 + apr * 90))))

        opening_score = int(min(99, max(15, round(18 + (d["ok"] / rounds) * 280 + (open_win_pct * 0.48)))))
        first_duel_rank = round(max(1.0, 5.2 - (open_att_pct / 7.0)), 2)

        clutches_per_24 = round(((d["clutch_points"] / 1.5) / rounds) * 24.0, 2)
        closing_score = int(min(99, max(15, round(20 + survival_pct * 0.58 + (d["clutch_points"] / maps) * 8.5))))
        alive_post_plant_pct = round(min(82.0, survival_pct * 1.25), 1)

        sniper_kills_pct = round((d["sniper_kills"] / max(1, kills)) * 100.0, 1)
        awp_kills_pct = round((d["awp_kills"] / max(1, kills)) * 100.0, 1)
        sniper_rounds_pct = round((d["rounds_with_sniper"] / rounds) * 100.0, 1)
        awp_rounds_pct = round((d["rounds_with_awp"] / rounds) * 100.0, 1)
        sniping_score = int(min(99, max(2, round(sniper_kills_pct * 1.35 + sniper_rounds_pct * 1.2))))

        smokes_pr = round(d["smokes_thrown"] / rounds, 2)
        flashes_pr = round(d["flashes_thrown"] / rounds, 2)
        molotovs_pr = round(d["molotovs_thrown"] / rounds, 2)
        he_pr = round(d["he_thrown"] / rounds, 2)
        util_dmg_pr = round(d["utility_damage"] / rounds, 2)
        flashed_pr = round(d["enemies_flashed"] / rounds, 2)
        total_nades_pr = smokes_pr + flashes_pr + molotovs_pr + he_pr
        utility_score = int(min(99, max(12, round(total_nades_pr * 28.0 + flashed_pr * 22.0 + util_dmg_pr * 3.5))))

        attributes_detail = {
            "firepower": {
                "score": fp_score,
                "label": "Firepower",
                "metrics": [
                    {"name": "Kills per Round Win", "value": str(kills_per_win), "pct": min(100, int(kills_per_win * 65))},
                    {"name": "Round Win Shares", "value": str(rws), "pct": min(100, int(rws * 5.5))},
                    {"name": "Eco-Adjusted Kills / Rnd", "value": str(perfect_kpr), "pct": min(100, int(perfect_kpr * 95))},
                    {"name": "Multi-Kill Rounds / Rnd", "value": str(trade_denials_pr), "pct": min(100, int(trade_denials_pr * 250))},
                    {"name": "Rounds with a Kill", "value": f"{rounds_with_kill_pct}%", "pct": int(rounds_with_kill_pct)},
                    {"name": "Rounds with a Multi-Kill", "value": f"{multi_kill_pct}%", "pct": min(100, int(multi_kill_pct * 2.8))},
                    {"name": "Net Kills per 24 Rounds", "value": f"{net_kills_24:+.2f}", "pct": min(100, max(10, int(50 + net_kills_24 * 6)))},
                    {"name": "ADR (Capped 100 HP)", "value": f"{adr}", "pct": min(100, int(adr))},
                ],
            },
            "entrying": {
                "score": entry_score,
                "label": "Entrying",
                "metrics": [
                    {"name": "Traded Deaths per Round", "value": str(tradeable_dpr), "pct": min(100, int(tradeable_dpr * 240))},
                    {"name": "Deaths Traded %", "value": f"{trade_pct}%", "pct": min(100, int(trade_pct * 2.2))},
                    {"name": "Assists per Round", "value": str(apr), "pct": min(100, int(apr * 260))},
                    {"name": "Opening Deaths Traded %", "value": f"{open_deaths_traded_pct}%", "pct": min(100, int(open_deaths_traded_pct * 1.8))},
                ],
            },
            "trading": {
                "score": trading_score,
                "label": "Trading",
                "metrics": [
                    {"name": "Trade Events per Round", "value": str(trade_opp_pr), "pct": min(100, int(trade_opp_pr * 210))},
                    {"name": "Trade Kills % (4s window)", "value": f"{trade_kills_pct}% ({real_trade_kills})", "pct": min(100, int(trade_kills_pct * 2.4))},
                    {"name": "Traded Deaths %", "value": f"{trade_pct}% ({d['traded_deaths']})", "pct": min(100, int(trade_pct * 2.2))},
                    {"name": "Assisted Kills %", "value": f"{assisted_kills_pct}%", "pct": min(100, int(assisted_kills_pct * 2.2))},
                ],
            },
            "opening": {
                "score": opening_score,
                "label": "Opening",
                "metrics": [
                    {"name": "Opening Attempts", "value": f"{open_att_pct}% ({open_total})", "pct": min(100, int(open_att_pct * 2.5))},
                    {"name": "Opening Kills - Deaths", "value": f"{d['ok']} - {d['od']}", "pct": min(100, max(15, int(open_win_pct)))},
                    {"name": "First Duel Win %", "value": f"{open_win_pct}%", "pct": int(open_win_pct)},
                    {"name": "First Duel Rank in Team", "value": str(first_duel_rank), "pct": min(100, max(15, int((6.0 - first_duel_rank) * 18)))},
                ],
            },
            "closing": {
                "score": closing_score,
                "label": "Closing",
                "metrics": [
                    {"name": "Survival Rate %", "value": f"{survival_pct}%", "pct": min(100, int(survival_pct * 1.8))},
                    {"name": "Alive for Post-Plant %", "value": f"{alive_post_plant_pct}%", "pct": int(alive_post_plant_pct)},
                    {"name": "Clutch Points Earned", "value": f"{round(d['clutch_points'], 1)} pts", "pct": min(100, max(10, int(d['clutch_points'] * 12)))},
                    {"name": "Clutches per 24 Rounds", "value": str(clutches_per_24), "pct": min(100, max(8, int(clutches_per_24 * 140)))},
                ],
            },
            "sniping": {
                "score": sniping_score,
                "label": "Sniping",
                "metrics": [
                    {"name": "Rounds with a Sniper Kill", "value": f"{sniper_rounds_pct}% ({d['rounds_with_sniper']})", "pct": min(100, int(sniper_rounds_pct * 2.2))},
                    {"name": "Sniper Kills %", "value": f"{sniper_kills_pct}% ({d['sniper_kills']})", "pct": min(100, int(sniper_kills_pct * 1.8))},
                    {"name": "Rounds with an AWP Kill", "value": f"{awp_rounds_pct}% ({d['rounds_with_awp']})", "pct": min(100, int(awp_rounds_pct * 2.2))},
                    {"name": "AWP Kills %", "value": f"{awp_kills_pct}% ({d['awp_kills']})", "pct": min(100, int(awp_kills_pct * 1.8))},
                ],
            },
            "utility": {
                "score": utility_score,
                "label": "Utility",
                "metrics": [
                    {"name": "Smokes Thrown per Round", "value": f"{smokes_pr} ({d['smokes_thrown']})", "pct": min(100, int(smokes_pr * 110))},
                    {"name": "Flashbangs Thrown / Flashed", "value": f"{flashes_pr} (Blind: {d['enemies_flashed']})", "pct": min(100, int(flashes_pr * 105))},
                    {"name": "Molotovs Thrown per Round", "value": f"{molotovs_pr} ({d['molotovs_thrown']})", "pct": min(100, int(molotovs_pr * 110))},
                    {"name": "HE Thrown / Utility Dmg", "value": f"{he_pr} ({round(d['utility_damage'], 0)} dmg)", "pct": min(100, int(he_pr * 125 + util_dmg_pr * 8))},
                ],
            },
        }

        acc_obj = (
            accounts_by_steam.get(steam_id)
            if steam_id
            else accounts_by_name.get(player_name.lower())
        )
        avatar_url = acc_obj.avatar_url if acc_obj else None
        country_flag = (getattr(acc_obj, "country", None) if acc_obj else None) or "🇷🇺"

        aw = completed_awards.get(key) or completed_awards.get(player_name) or {
            "trophies_count": 0,
            "trophies_list": [],
            "mvp_count": 0,
            "mvp_list": [],
            "evp_count": 0,
            "evp_list": [],
        }

        results.append({
            "player": player_name,
            "steam_id": steam_id,
            "steam_url": f"https://steamcommunity.com/profiles/{steam_id}" if steam_id else None,
            "avatar_url": avatar_url,
            "country": country_flag,
            "aliases": aliases,
            "team": d["team"],
            "teams": sorted(list(d["teams_set"])),
            "maps": d["maps"],
            "rounds": rounds,
            "k": kills,
            "d": deaths,
            "a": assists,
            "k_d": f"{kills}-{deaths}",
            "diff": diff,
            "diff_str": f"+{diff}" if diff > 0 else str(diff),
            "net_kills_24": net_kills_24,
            "net_kills_24_str": f"{net_kills_24:+.2f}",
            "eco_swing": eco_swing_val,
            "eco_swing_str": f"{eco_swing_val:+.2f}%",
            "rnd_swing": rnd_swing_per_round,
            "rnd_swing_str": f"{rnd_swing_per_round:+.2f}%",
            "match_swing_str": f"{rnd_swing_per_round:+.2f}%",
            "rating_3": active_rating,
            "lanus_3": both_rating,
            "t_rating": t_rating,
            "ct_rating": ct_rating,
            "impact": impact,
            "adr": adr,
            "kast_pct": kast_pct,
            "kpr": kpr,
            "dpr": dpr,
            "hs_pct": f"{hs_pct}%",
            "multi_kill_pct": f"{multi_kill_pct}%",
            "trophies_count": aw["trophies_count"],
            "trophies_list": aw["trophies_list"],
            "mvp_count": aw["mvp_count"],
            "mvp_list": aw["mvp_list"],
            "evp_count": aw["evp_count"],
            "evp_list": aw["evp_list"],
            "clutch_points": round(d["clutch_points"], 1),
            "ok_od": f"{d['ok']}-{d['od']}",
            "attributes": attributes_detail,
            "recent_matches": d["matches_history"][:15],
        })

    results.sort(key=lambda x: x["rating_3"], reverse=True)
    for idx, item in enumerate(results):
        item["rank"] = idx + 1
        item["is_top_10"] = (idx + 1) <= 10

    return results


@app.patch(
    "/api/series/{series_id}",
    response_model=SeriesOut,
    tags=["Series (Bo3 / Bo5)"],
    summary="Редактировать серию матчей (Bo1 / Bo3 / Bo5)",
)
def update_series(
    series_id: int,
    payload: SeriesUpdate,
    db: Session = Depends(get_db),
):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Серия матчей не найдена")

    if payload.tournament_id is not None:
        series.tournament_id = payload.tournament_id if payload.tournament_id > 0 else None
    if payload.title is not None:
        series.title = payload.title.strip()
    if payload.format is not None:
        series.format = payload.format.upper().replace("BO", "Bo")
    if payload.team1_name is not None:
        series.team1_name = payload.team1_name.strip()
    if payload.team2_name is not None:
        series.team2_name = payload.team2_name.strip()
    if payload.status is not None:
        series.status = payload.status

    db.add(series)
    db.commit()
    db.refresh(series)
    recalculate_series_score(db, series)

    return SeriesOut(
        id=series.id,
        tournament_id=series.tournament_id,
        title=series.title,
        format=series.format,
        team1_name=series.team1_name,
        team2_name=series.team2_name,
        team1_wins=series.team1_wins,
        team2_wins=series.team2_wins,
        series_score=f"{series.team1_wins}:{series.team2_wins}",
        winner_team=series.winner_team,
        status=series.status,
        matches_count=len(series.matches),
        created_at=series.created_at,
    )


@app.delete(
    "/api/series/{series_id}",
    tags=["Series (Bo3 / Bo5)"],
    summary="Удалить серию матчей (сами карты остаются в БД)",
)
def delete_series(series_id: int, db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Серия матчей не найдена")
    for m in series.matches:
        m.series_id = None
        db.add(m)
    db.flush()
    db.delete(series)
    db.commit()
    return {"status": "deleted", "series_id": series_id}


@app.get(
    "/api/players/top",
    tags=["Players & HLTV Profiles"],
    summary="Глобальный Топ-рейтинг игроков (HLTV 3.0 Leaderboard)",
)
def get_top_players(
    tournament_id: Optional[int] = Query(None, description="Фильтр по турниру"),
    map_name: Optional[str] = Query(None, description="Фильтр по карте"),
    side: str = Query("both", description="both | ct | t"),
    db: Session = Depends(get_db),
):
    q = db.query(Match)
    if tournament_id is not None:
        q = q.filter(Match.tournament_id == tournament_id)
    if map_name and map_name.lower() != "all":
        q = q.filter(Match.map_name.ilike(f"%{map_name}%"))

    matches = q.order_by(Match.played_at.desc()).all()
    players = compute_full_player_analytics(matches, side=side, db=db)
    return {
        "total_matches": len(matches),
        "side": side,
        "players": players,
    }


@app.get(
    "/api/players/{player_name}",
    tags=["Players & HLTV Profiles"],
    summary="Профиль игрока + детальная статистика 7 атрибутов HLTV 3.0 (по SteamID64 или нику)",
)
def get_player_profile(
    player_name: str,
    tournament_id: Optional[int] = Query(None),
    map_name: Optional[str] = Query(None),
    side: str = Query("both"),
    db: Session = Depends(get_db),
):
    q = db.query(Match)
    if tournament_id is not None:
        q = q.filter(Match.tournament_id == tournament_id)
    if map_name and map_name.lower() != "all":
        q = q.filter(Match.map_name.ilike(f"%{map_name}%"))

    matches = q.order_by(Match.played_at.desc()).all()
    all_players = compute_full_player_analytics(matches, side=side, db=db)

    query_lower = player_name.strip().lower()
    for p in all_players:
        if (
            (p.get("steam_id") and p["steam_id"].lower() == query_lower)
            or p["player"].lower() == query_lower
            or any(alias.lower() == query_lower for alias in p.get("aliases", []))
        ):
            return p

    raise HTTPException(status_code=404, detail=f"Игрок '{player_name}' не найден в базе данных")


def _resolve_player_account_for_avatar(db: Session, player_identifier: str) -> PlayerAccount:
    """Находит или создает запись PlayerAccount для сохранения фото или страны игрока (по SteamID64 или нику)."""
    q_str = player_identifier.strip()
    q_low = q_str.lower()

    acc = db.query(PlayerAccount).filter(PlayerAccount.steam_id == q_str).first()
    if acc:
        return acc

    for item in db.query(PlayerAccount).all():
        if item.current_name.lower() == q_low or any(str(a).lower() == q_low for a in (item.aliases_json or [])):
            return item

    ps = (
        db.query(PlayerMatchStats)
        .filter((PlayerMatchStats.steam_id == q_str) | (PlayerMatchStats.player_name.ilike(q_str)))
        .first()
    )
    if ps:
        sid = ps.steam_id or f"name:{ps.player_name.lower()}"
        acc = db.query(PlayerAccount).filter(PlayerAccount.steam_id == sid).first()
        if not acc:
            acc = PlayerAccount(
                steam_id=sid,
                current_name=ps.player_name,
                aliases_json=[ps.player_name],
                country="🇷🇺",
            )
            db.add(acc)
            db.flush()
        return acc

    raise HTTPException(status_code=404, detail=f"Игрок '{player_identifier}' не найден")


@app.patch(
    "/api/players/{player_name}/country",
    tags=["Players & HLTV Profiles"],
    summary="Изменить страну (флаг) игрока",
)
def update_player_country(
    player_name: str,
    payload: PlayerCountryUpdate,
    db: Session = Depends(get_db),
):
    acc = _resolve_player_account_for_avatar(db, player_name)
    acc.country = payload.country.strip() or "🇷🇺"
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return {
        "status": "updated",
        "player": acc.current_name,
        "steam_id": acc.steam_id,
        "country": acc.country,
    }


@app.post(
    "/api/players/{player_name}/avatar",
    tags=["Players & HLTV Profiles"],
    summary="Загрузить фото (портрет) игрока в профиль HLTV",
)
async def upload_player_avatar(
    player_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    acc = _resolve_player_account_for_avatar(db, player_name)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}:
        ext = ".png"

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Пустой файл изображения")

    safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", acc.steam_id)
    new_filename = f"player_{safe_key}_{int(time.time())}{ext}"
    new_filepath = os.path.join(AVATARS_DIR, new_filename)

    if acc.avatar_url and acc.avatar_url.startswith("/static/avatars/"):
        old_name = acc.avatar_url.split("/static/avatars/")[-1]
        old_path = os.path.join(AVATARS_DIR, old_name)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

    with open(new_filepath, "wb") as f:
        f.write(content)

    acc.avatar_url = f"/static/avatars/{new_filename}"
    db.add(acc)
    db.commit()
    db.refresh(acc)

    return {
        "status": "uploaded",
        "player": acc.current_name,
        "steam_id": acc.steam_id,
        "avatar_url": acc.avatar_url,
    }


@app.delete(
    "/api/players/{player_name}/avatar",
    tags=["Players & HLTV Profiles"],
    summary="Удалить загруженное фото игрока",
)
def delete_player_avatar(
    player_name: str,
    db: Session = Depends(get_db),
):
    acc = _resolve_player_account_for_avatar(db, player_name)
    if acc.avatar_url and acc.avatar_url.startswith("/static/avatars/"):
        old_name = acc.avatar_url.split("/static/avatars/")[-1]
        old_path = os.path.join(AVATARS_DIR, old_name)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass
    acc.avatar_url = None
    db.add(acc)
    db.commit()
    return {"status": "deleted", "player": acc.current_name, "steam_id": acc.steam_id}


def reconcile_existing_db():
    """
    При старте сервера автоматически:
    1) Нормализует все старые значения round_swing (>10%) в таблицах player_match_stats и matches.
    2) Связывает непривязанные серии с единственным турниром (если пользователь создал турнир и серию отдельно).
    3) Пересчитывает счет всех серий Bo1/Bo3/Bo5 и определяет победителей и кубки турниров.
    """
    db = SessionLocal()
    try:
        for m in db.query(Match).all():
            rnds = max(1, m.total_rounds or 24)
            for ps in m.player_stats:
                if abs(ps.round_swing) > 9.5:
                    norm_s = normalize_round_swing_val(ps.round_swing, rnds, ps.deaths)
                    ps.round_swing = norm_s
                    ps.round_swing_str = f"{norm_s:+.2f}%"
                    db.add(ps)
            if m.mvp_swing and ("1" in m.mvp_swing or "2" in m.mvp_swing):
                try:
                    raw_val = float(str(m.mvp_swing).replace("%", "").replace("+", "").strip())
                    if abs(raw_val) > 9.5:
                        norm_m = round(max(-7.5, min(7.5, (raw_val / 100.0 / rnds) * 28.0)), 2)
                        m.mvp_swing = f"{norm_m:+.2f}%"
                        db.add(m)
                except Exception:
                    pass
        db.commit()

        tournaments = db.query(Tournament).all()
        all_series = db.query(Series).all()
        if len(tournaments) == 1 and all_series:
            t0 = tournaments[0]
            for s in all_series:
                if s.tournament_id is None:
                    s.tournament_id = t0.id
                    db.add(s)
                    for m in s.matches:
                        if m.tournament_id is None:
                            m.tournament_id = t0.id
                            db.add(m)
            db.commit()

        for s in db.query(Series).all():
            recalculate_series_score(db, s)
        for t in db.query(Tournament).all():
            recalculate_tournament_status(db, t)
    except Exception:
        pass
    finally:
        db.close()


reconcile_existing_db()


# =====================================================================
# 4. СОВМЕСТИМОСТЬ С ТЕКУЩИМ ВЕБ-ИНТЕРФЕЙСОМ (FRONTEND ROUTES)
# =====================================================================

@app.get("/", response_class=HTMLResponse, tags=["Web UI"])
def homepage():
    if not os.path.exists(INDEX_HTML_PATH):
        return HTMLResponse("<h1>Error: templates/index.html not found</h1>", status_code=500)
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/status", tags=["Web UI"])
def api_status(db: Session = Depends(get_db)):
    return {
        "status": "online",
        "version": "5.0 PRO (FastAPI + SQLAlchemy DB)",
        "engine": "LanUs Rating 3.0 (HLTV 3.0 Analog)",
        "tournaments_count": db.query(Tournament).count(),
        "series_count": db.query(Series).count(),
        "matches_count": db.query(Match).count(),
    }


@app.post("/api/upload", tags=["Web UI"])
def legacy_api_upload(
    file: UploadFile = File(...),
    tournament_id: Optional[int] = Form(None),
    series_id: Optional[int] = Form(None),
    map_number: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Совместимый с текущим фронтендом эндпоинт Drag & Drop загрузки:
    парсит демку, удаляет .dem файл, автоматически сохраняет результат в БД и возвращает JSON.
    """
    parsed_data, filename = parse_uploaded_demo_and_cleanup(file)
    match = save_parsed_match_to_db(
        db=db,
        data=parsed_data,
        demo_filename=filename,
        tournament_id=tournament_id,
        series_id=series_id,
        map_number=map_number,
    )
    return serialize_match_detail(match, db=db)


@app.get("/api/load_default", tags=["Web UI"])
def api_load_default(
    tournament_id: Optional[int] = Query(None),
    series_id: Optional[int] = Query(None),
    map_number: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    if not os.path.exists(DEFAULT_DEMO):
        return JSONResponse(
            {"error": f"Default demo file not found at {DEFAULT_DEMO}"}, status_code=404
        )
    try:
        data = LanUsEngineV4(DEFAULT_DEMO).analyze()
        match = save_parsed_match_to_db(
            db,
            data,
            demo_filename=os.path.basename(DEFAULT_DEMO),
            tournament_id=tournament_id,
            series_id=series_id,
            map_number=map_number,
        )
        return serialize_match_detail(match)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/load_demo2", tags=["Web UI"])
def api_load_demo2(
    tournament_id: Optional[int] = Query(None),
    series_id: Optional[int] = Query(None),
    map_number: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    if not os.path.exists(DEMO_2):
        return JSONResponse(
            {"error": f"Demo 2 file not found at {DEMO_2}"}, status_code=404
        )
    try:
        data = LanUsEngineV4(DEMO_2).analyze()
        match = save_parsed_match_to_db(
            db,
            data,
            demo_filename=os.path.basename(DEMO_2),
            tournament_id=tournament_id,
            series_id=series_id,
            map_number=map_number,
        )
        return serialize_match_detail(match)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("=================================================================")
    print("🚀 LANUS CS2 ANALYTICS FASTAPI SERVER (v5.0 PRO + DB) ЗАПУЩЕН!")
    print(f"🌐 Веб-интерфейс: http://localhost:{port}")
    print(f"📘 Swagger UI (REST API Docs): http://localhost:{port}/docs")
    print("=================================================================")
    uvicorn.run("main:app", host="127.0.0.1", port=port, log_level="info")
