"""
SQLAlchemy ORM Models for LanUs Analytics:
- Tournament (Турниры)
- Series (Серии матчей: Bo1, Bo3, Bo5)
- Match (Отдельные карты, распарсенные из .dem)
- PlayerMatchStats (Статистика игроков по системе LanUs Rating 3.0)
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    Text,
    JSON,
)
from sqlalchemy.orm import relationship
from database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Tournament(Base):
    __tablename__ = "tournaments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    prize_pool = Column(String(100), nullable=True)
    status = Column(String(50), default="ongoing")  # upcoming | ongoing | completed
    winner_team = Column(String(120), nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    series_list = relationship(
        "Series",
        back_populates="tournament",
        cascade="all, delete-orphan",
        order_by="Series.created_at.desc()",
    )
    matches = relationship(
        "Match",
        back_populates="tournament",
        order_by="Match.played_at.desc()",
    )


class Series(Base):
    """
    Серия матчей (Bo1, Bo3, Bo5).
    Хранит общий счет по картам и объединяет до 5 отдельных карт (Match).
    """
    __tablename__ = "series"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(
        Integer, ForeignKey("tournaments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title = Column(String(200), nullable=True)  # Например: "Grand Final", "Upper Bracket R1"
    format = Column(String(10), default="Bo3", nullable=False)  # Bo1 | Bo3 | Bo5
    team1_name = Column(String(120), nullable=False)
    team2_name = Column(String(120), nullable=False)
    team1_wins = Column(Integer, default=0, nullable=False)
    team2_wins = Column(Integer, default=0, nullable=False)
    winner_team = Column(String(120), nullable=True)
    status = Column(String(50), default="ongoing", nullable=False)  # ongoing | completed
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    tournament = relationship("Tournament", back_populates="series_list")
    matches = relationship(
        "Match",
        back_populates="series",
        cascade="all, delete-orphan",
        order_by="Match.map_number.asc(), Match.played_at.asc()",
    )

    @property
    def maps_needed_to_win(self) -> int:
        fmt = (self.format or "Bo3").upper()
        if fmt == "BO1":
            return 1
        if fmt == "BO5":
            return 3
        return 2  # Default Bo3


class Match(Base):
    """
    Результаты парсинга одной карты (.dem файла).
    Сам .dem файл удаляется сразу после анализа, в БД хранятся только готовые метрики.
    """
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(
        Integer, ForeignKey("tournaments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    series_id = Column(
        Integer, ForeignKey("series.id", ondelete="SET NULL"), nullable=True, index=True
    )
    map_number = Column(Integer, default=1, nullable=False)  # Номер карты в серии (1..5)

    # Метаданные карты
    demo_filename = Column(String(255), nullable=True)
    map_name = Column(String(100), nullable=False, index=True)
    server_name = Column(String(255), nullable=True)
    total_rounds = Column(Integer, nullable=False)

    # Команды и счёт
    team1_name = Column(String(120), nullable=False)
    team2_name = Column(String(120), nullable=False)
    team1_score = Column(Integer, nullable=False)
    team2_score = Column(Integer, nullable=False)
    final_score = Column(String(30), nullable=False)
    winner_team = Column(String(120), nullable=True)

    # MVP и системная информация
    mvp_player = Column(String(120), nullable=True)
    mvp_rating = Column(Float, nullable=True)
    filter_message = Column(Text, nullable=True)

    # JSON-поля для мгновенной отдачи таймлайна раундов и пьедестала без лишних джойнов
    round_history_json = Column(JSON, nullable=True)
    evps_json = Column(JSON, nullable=True)

    played_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    # Relationships
    tournament = relationship("Tournament", back_populates="matches")
    series = relationship("Series", back_populates="matches")
    player_stats = relationship(
        "PlayerMatchStats",
        back_populates="match",
        cascade="all, delete-orphan",
        order_by="PlayerMatchStats.lanus_3.desc()",
    )


class PlayerAccount(Base):
    """
    Глобальный профиль игрока, привязанный к неизменяемому SteamID64 (steam_id).
    Защищает статистику игрока в БД при смене никнейма в Steam/CS2 и хранит фото игрока.
    """
    __tablename__ = "player_accounts"

    steam_id = Column(String(64), primary_key=True, index=True)
    current_name = Column(String(120), nullable=False, index=True)
    aliases_json = Column(JSON, default=list, nullable=False)  # История старых никнеймов
    avatar_url = Column(String(500), nullable=True)  # Путь к загруженному фото игрока (/static/avatars/...)
    country = Column(String(32), default="🇷🇺", nullable=False)  # Флаг / страна игрока
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class PlayerMatchStats(Base):
    """
    Детальная статистика игрока за конкретный матч по математическому ядру LanUs Rating 3.0.
    Привязывается к игроку по steam_id (SteamID64), чтобы смена ника не ломала агрегацию.
    """
    __tablename__ = "player_match_stats"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(
        Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )

    steam_id = Column(String(64), nullable=True, index=True)
    player_name = Column(String(120), nullable=False, index=True)
    team_name = Column(String(120), nullable=False, index=True)

    # Базовые показатели
    kills = Column(Integer, nullable=False)
    deaths = Column(Integer, nullable=False)
    assists = Column(Integer, nullable=False)
    diff = Column(Integer, nullable=False)
    hs_pct = Column(String(20), nullable=False)
    adr = Column(Float, nullable=False)
    kast_pct = Column(Float, nullable=False)

    # LanUs Rating 3.0 & Стороны
    lanus_3 = Column(Float, nullable=False, index=True)
    t_rating = Column(Float, nullable=False)
    ct_rating = Column(Float, nullable=False)
    impact = Column(Float, nullable=False)

    # Eco-Adjustment & Round Swing
    eco_kills = Column(Float, nullable=False)
    eco_delta = Column(Float, nullable=False)
    eco_kills_impact = Column(String(50), nullable=False)
    round_swing = Column(Float, nullable=False)  # в процентах, напр. 18.4
    round_swing_str = Column(String(30), nullable=False)

    # Энтри-дуэли, мультикиллы, клатчи, размены
    opening_kills = Column(Integer, default=0, nullable=False)
    opening_deaths = Column(Integer, default=0, nullable=False)
    ok_od = Column(String(20), nullable=False)
    pressure_kills = Column(Integer, default=0, nullable=False)
    anti_eco_kills = Column(Integer, default=0, nullable=False)
    multi_2k = Column(Integer, default=0, nullable=False)
    multi_3k = Column(Integer, default=0, nullable=False)
    multi_4k = Column(Integer, default=0, nullable=False)
    clutches_str = Column(String(100), default="—", nullable=False)
    clutch_points = Column(Float, default=0.0, nullable=False)
    traded_deaths = Column(Integer, default=0, nullable=False)
    trade_kills = Column(Integer, default=0, nullable=False)
    trade_pct = Column(Float, default=0.0, nullable=False)

    # Реальная телеметрия снайперских винтовок и гранат из .dem
    awp_kills = Column(Integer, default=0, nullable=False)
    sniper_kills = Column(Integer, default=0, nullable=False)
    rounds_with_awp = Column(Integer, default=0, nullable=False)
    rounds_with_sniper = Column(Integer, default=0, nullable=False)
    smokes_thrown = Column(Integer, default=0, nullable=False)
    flashes_thrown = Column(Integer, default=0, nullable=False)
    molotovs_thrown = Column(Integer, default=0, nullable=False)
    he_thrown = Column(Integer, default=0, nullable=False)
    enemies_flashed = Column(Integer, default=0, nullable=False)
    utility_damage = Column(Float, default=0.0, nullable=False)

    # 6 нормированных суб-факторов LanUs Rating 3.0
    r_kill_eco = Column(Float, nullable=False)
    r_survival = Column(Float, nullable=False)
    r_damage_eco = Column(Float, nullable=False)
    r_roundswing = Column(Float, nullable=False)
    r_impact = Column(Float, nullable=False)
    r_kast = Column(Float, nullable=False)

    # Relationship
    match = relationship("Match", back_populates="player_stats")
