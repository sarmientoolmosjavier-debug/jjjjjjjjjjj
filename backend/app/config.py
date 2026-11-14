"""Configuración desde variables de entorno.

Los valores de riesgo aquí son los *por defecto*; los que el usuario cambie desde el panel se guardan
en la base de datos (tabla ``settings``) y prevalecen. Las credenciales nunca se guardan en la BD.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Infraestructura -------------------------------------------------------------
    database_url: str = "sqlite:///./data/copytrader.db"
    admin_token: SecretStr | None = None  # protege los endpoints que modifican estado
    app_secret: SecretStr = SecretStr("cambia-esto")  # firma de la autorización persistente
    cors_origins: str = "http://localhost:3000"
    notify_webhook_url: str | None = None  # p. ej. ntfy.sh/<tema> (gratis); vacío = sólo log
    webhook_secret: SecretStr | None = None  # HMAC de las señales propias (fuente "manual")
    hyperliquid_info_url: str = "https://api.hyperliquid.xyz/info"

    # --- Coinbase ---------------------------------------------------------------------
    coinbase_api_key_name: str | None = None  # "organizations/.../apiKeys/..."
    coinbase_api_private_key: SecretStr | None = None  # PEM EC (ECDSA, ES256)
    coinbase_api_key_file: str | None = None  # JSON descargado de CDP (alternativa)
    coinbase_rest_base: str = "https://api.coinbase.com/api/v3/brokerage"
    coinbase_ws_url: str = "wss://advanced-trade-ws.coinbase.com"

    # --- Modo real ---------------------------------------------------------------------
    live_trading_allowed: bool = False  # interruptor maestro: sin él el modo real no existe
    live_persist_auth: bool = False  # permite reanudar el modo real tras reinicio con autorización firmada
    live_persist_auth_hours: int = 24

    # --- Cartera y riesgo (valores por defecto) ---------------------------------------
    initial_capital_eur: Decimal = Decimal("100")
    min_cash_reserve_eur: Decimal = Decimal("30")
    max_total_exposure_eur: Decimal = Decimal("70")
    max_asset_exposure_eur: Decimal = Decimal("20")
    max_open_positions: int = 4
    max_new_trades_per_day: int = 3
    max_daily_loss_eur: Decimal = Decimal("2")
    max_drawdown_eur: Decimal = Decimal("10")
    max_round_trip_cost_pct: Decimal = Decimal("1.0")
    min_trade_eur: Decimal = Decimal("5")
    order_style: str = "maker"  # maker (limit post-only) | taker (market IOC)
    maker_order_ttl_seconds: int = 300
    max_signal_age_seconds: int = 120
    max_quote_age_seconds: int = 15
    min_slippage_bps: Decimal = Decimal("2")
    max_spread_pct: Decimal = Decimal("0.30")
    min_trader_score: Decimal = Decimal("60")
    allowed_assets: str = "BTC,ETH"
    # Estimación conservadora hasta leer la comisión real (transaction_summary).
    default_maker_fee: Decimal = Decimal("0.0060")
    default_taker_fee: Decimal = Decimal("0.0120")
    hl_include_perp_longs: bool = True  # señales derivadas de largos en perpetuos (etiquetadas)
    poll_interval_seconds: int = 30

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Ajustes editables desde el panel (nombre -> tipo). Todo lo demás sólo por entorno.
EDITABLE = {
    "min_cash_reserve_eur": Decimal,
    "max_total_exposure_eur": Decimal,
    "max_asset_exposure_eur": Decimal,
    "max_open_positions": int,
    "max_new_trades_per_day": int,
    "max_daily_loss_eur": Decimal,
    "max_drawdown_eur": Decimal,
    "max_round_trip_cost_pct": Decimal,
    "min_trade_eur": Decimal,
    "order_style": str,
    "max_signal_age_seconds": int,
    "max_spread_pct": Decimal,
    "min_trader_score": Decimal,
    "allowed_assets": str,
    "hl_include_perp_longs": bool,
}

# Topes duros: el panel no puede superarlos (protegen el perfil "moderado").
HARD_LIMITS = {
    "max_total_exposure_eur": Decimal("70"),
    "max_asset_exposure_eur": Decimal("20"),
    "max_open_positions": 4,
    "max_new_trades_per_day": 3,
    "max_daily_loss_eur": Decimal("2"),
    "max_drawdown_eur": Decimal("10"),
}
HARD_MINIMUMS = {"min_cash_reserve_eur": Decimal("30")}

_ = Field  # reexport silencioso
