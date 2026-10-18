from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Telemetry(Payload):
    device_id: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    sample_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    timestamp: datetime
    soil_moisture: float | None = Field(default=None, ge=0, le=100)
    temperature: float | None = Field(default=None, ge=-40, le=80)
    humidity: float | None = Field(default=None, ge=0, le=100)
    light_intensity: float | None = Field(default=None, ge=0, le=65535)
    pump_on: bool = False
    sensor_ok: bool = True
    reservoir_ok: bool = True
    simulated: bool = False

    @model_validator(mode='after')
    def validate_reading(self):
        if self.timestamp.tzinfo is None:
            raise ValueError('timestamp phải có múi giờ ISO 8601')
        if self.sensor_ok and any(getattr(self, k) is None for k in ('soil_moisture', 'temperature', 'humidity', 'light_intensity')):
            raise ValueError('Thiếu giá trị cảm biến khi sensor_ok=true')
        return self


class Mode(Payload):
    device_id: str
    mode: Literal['manual', 'rule', 'ai']


class Pump(Payload):
    device_id: str
    action: Literal['start', 'stop']
    duration_seconds: int = Field(default=5, ge=1, le=30)


class Ack(Payload):
    device_id: str
    status: Literal['started', 'completed', 'rejected', 'stopped']
    reason: str = Field(default='', max_length=300)
    actual_duration_seconds: float | None = Field(default=None, ge=0, le=31)
