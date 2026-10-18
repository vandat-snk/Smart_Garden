from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://garden:CHANGE_ME@127.0.0.1:55432/smart_garden'
    admin_token: str = Field(min_length=24)
    device_key: str = Field(min_length=24)
    device_id: str = 'garden-01'
    max_pump_seconds: int = Field(default=30, ge=1, le=30)
    default_pump_seconds: int = Field(default=5, ge=1, le=30)
    cooldown_seconds: int = Field(default=300, ge=30)
    stale_seconds: int = Field(default=60, ge=10, le=120)
    soil_start_threshold: float = Field(default=30, ge=0, le=100)
    soil_stop_threshold: float = Field(default=60, ge=0, le=100)
    model_dir: str = 'models/current'
    allow_experimental_ai: bool = False

    @model_validator(mode='after')
    def validate_settings(self):
        if self.soil_start_threshold >= self.soil_stop_threshold:
            raise ValueError('Ngưỡng bắt đầu phải nhỏ hơn ngưỡng dừng')
        if self.default_pump_seconds > self.max_pump_seconds:
            raise ValueError('Thời gian mặc định vượt giới hạn bơm')
        if self.admin_token == self.device_key:
            raise ValueError('Khóa quản trị và thiết bị phải khác nhau')
        return self
