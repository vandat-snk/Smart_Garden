from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = 'devices'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default='manual')
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_water: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prediction: Mapped[dict | None] = mapped_column(JSON)


class Reading(Base):
    __tablename__ = 'readings'
    __table_args__ = (UniqueConstraint('device_id', 'sample_id'), Index('ix_readings_device_time', 'device_id', 'timestamp'))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey('devices.id'))
    sample_id: Mapped[str] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    soil_moisture: Mapped[float | None] = mapped_column(Float)
    temperature: Mapped[float | None] = mapped_column(Float)
    humidity: Mapped[float | None] = mapped_column(Float)
    light_intensity: Mapped[float | None] = mapped_column(Float)
    pump_on: Mapped[bool] = mapped_column(Boolean)
    sensor_ok: Mapped[bool] = mapped_column(Boolean)
    reservoir_ok: Mapped[bool] = mapped_column(Boolean)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Command(Base):
    __tablename__ = 'commands'
    __table_args__ = (Index('ix_commands_device_time', 'device_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey('devices.id'))
    action: Mapped[str] = mapped_column(String(8))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default='queued')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_duration_seconds: Mapped[float | None] = mapped_column(Float)


def connect(url):
    kwargs = {'check_same_thread': False} if url.startswith('sqlite') else {}
    engine = create_engine(url, pool_pre_ping=True, connect_args=kwargs)
    return engine, sessionmaker(engine, expire_on_commit=False)
