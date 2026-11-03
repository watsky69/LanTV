"""
Pydantic Schemas for LanUs Analytics FastAPI Backend
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# =====================================================================
# TOURNAMENT SCHEMAS
# =====================================================================

class TournamentCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=200, description="Название турнира")
    description: Optional[str] = Field(None, description="Описание или регламент турнира")
    prize_pool: Optional[str] = Field(None, description="Призовой фонд (напр. '$10,000')")
    status: str = Field("ongoing", description="upcoming | ongoing | completed")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class TournamentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=200, description="Новое название турнира")
    description: Optional[str] = Field(None, description="Описание или регламент турнира")
    prize_pool: Optional[str] = Field(None, description="Призовой фонд")
    status: Optional[str] = Field(None, description="upcoming | ongoing | completed")
    winner_team: Optional[str] = Field(None, description="Команда-победитель турнира")


class TournamentSummaryOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    prize_pool: Optional[str]
    status: str
    winner_team: Optional[str] = None
    series_count: int = 0
    matches_count: int = 0
    created_at: datetime


class PlayerCountryUpdate(BaseModel):
    country: str = Field(..., min_length=1, max_length=32, description="Флаг / код страны игрока")


# =====================================================================
# SERIES (Bo1 / Bo3 / Bo5) SCHEMAS
# =====================================================================

class SeriesCreate(BaseModel):
    tournament_id: Optional[int] = Field(None, description="ID турнира (опционально)")
    title: Optional[str] = Field("Playoff Match", description="Стадия (напр. Grand Final, Semi-Final)")
    format: str = Field("Bo3", pattern="^(?i)(Bo1|Bo3|Bo5)$", description="Формат серии: Bo1, Bo3 или Bo5")
    team1_name: str = Field(..., min_length=1, max_length=120, description="Название первой команды")
    team2_name: str = Field(..., min_length=1, max_length=120, description="Название второй команды")


class SeriesUpdate(BaseModel):
    tournament_id: Optional[int] = None
    title: Optional[str] = None
    format: Optional[str] = None
    team1_name: Optional[str] = None
    team2_name: Optional[str] = None
    status: Optional[str] = None


class SeriesOut(BaseModel):
    id: int
    tournament_id: Optional[int]
    title: Optional[str]
    format: str
    team1_name: str
    team2_name: str
    team1_wins: int
    team2_wins: int
    series_score: str
    winner_team: Optional[str]
    status: str
    matches_count: int = 0
    created_at: datetime


# =====================================================================
# MATCH & PLAYER STATS SCHEMAS
# =====================================================================

class MatchAssignRequest(BaseModel):
    tournament_id: Optional[int] = Field(None, description="Привязать матч к турниру")
    series_id: Optional[int] = Field(None, description="Привязать матч к серии Bo1/Bo3/Bo5")
    map_number: Optional[int] = Field(None, ge=1, le=5, description="Номер карты в серии (1..5)")


class SubRatingsOut(BaseModel):
    r_kill_eco: float
    r_survival: float
    r_damage_eco: float
    r_roundswing: float
    r_impact: float
    r_kast: float


class PlayerCardOut(BaseModel):
    player: str
    team: str
    k_d: str
    k: int
    d: int
    a: int
    hs_pct: str
    diff: int
    diff_str: str
    adr: float
    kast_pct: float
    eco_kills: float
    eco_kills_impact: str
    eco_delta: float
    round_swing: float
    round_swing_str: str
    lanus_3: float
    t_rating: float
    ct_rating: float
    ok_od: str
    opening_kills: int
    opening_deaths: int
    pressure_kills: int
    anti_eco_kills: int
    multi_2k: int
    multi_3k: int
    multi_4k: int
    clutches_str: str
    clutch_points: float
    traded_deaths: int
    trade_pct: float
    impact: float
    sub_ratings: SubRatingsOut


class MatchSummaryOut(BaseModel):
    id: int
    tournament_id: Optional[int]
    series_id: Optional[int]
    map_number: int
    map_name: str
    server_name: Optional[str]
    total_rounds: int
    team1_name: str
    team2_name: str
    team1_score: int
    team2_score: int
    final_score: str
    winner_team: Optional[str]
    mvp_player: Optional[str]
    mvp_rating: Optional[float]
    played_at: datetime
