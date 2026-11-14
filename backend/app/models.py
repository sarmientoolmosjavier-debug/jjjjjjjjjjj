"""Esquema de datos. Importes en EUR con Numeric (Decimal), nunca float."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

N = Numeric(28, 12, asdecimal=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Trader(Base):
    __tablename__ = "traders"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # hyperliquid | manual
    external_id: Mapped[str] = mapped_column(String(128))  # dirección o id de la señal propia
    label: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # copia activada por el usuario
    verified: Mapped[bool] = mapped_column(Boolean, default=False)  # pasó los filtros de verificación
    score: Mapped[Decimal | None] = mapped_column(N, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluation_notes: Mapped[list] = mapped_column(JSON, default=list)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_polled_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"))
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(160))  # hash/tid on-chain o id del webhook
    asset: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    kind: Mapped[str] = mapped_column(String(32))  # spot | perp_long_open | perp_long_close
    trader_price: Mapped[Decimal] = mapped_column(N)
    trader_size: Mapped[Decimal] = mapped_column(N)
    trader_fraction: Mapped[Decimal | None] = mapped_column(N, nullable=True)  # % de su cuenta o de su posición
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"))
    mode: Mapped[str] = mapped_column(String(16))
    accepted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)  # idempotencia
    mode: Mapped[str] = mapped_column(String(16))  # simulation | live
    trader_id: Mapped[int | None] = mapped_column(ForeignKey("traders.id"), nullable=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    product_id: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    style: Mapped[str] = mapped_column(String(8))  # maker | taker
    quote_size: Mapped[Decimal | None] = mapped_column(N, nullable=True)
    base_size: Mapped[Decimal | None] = mapped_column(N, nullable=True)
    limit_price: Mapped[Decimal | None] = mapped_column(N, nullable=True)
    filled_base: Mapped[Decimal] = mapped_column(N, default=Decimal(0))
    avg_price: Mapped[Decimal | None] = mapped_column(N, nullable=True)
    fee_eur: Mapped[Decimal] = mapped_column(N, default=Decimal(0))
    realized_pnl_eur: Mapped[Decimal | None] = mapped_column(N, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING OPEN FILLED CANCELLED REJECTED FAILED
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("mode", "trader_id", "product_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"))
    product_id: Mapped[str] = mapped_column(String(32))
    base_qty: Mapped[Decimal] = mapped_column(N, default=Decimal(0))
    cost_eur: Mapped[Decimal] = mapped_column(N, default=Decimal(0))  # coste incl. comisión de entrada
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Wallet(Base):
    __tablename__ = "wallets"
    mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    cash_eur: Mapped[Decimal] = mapped_column(N)
    reserved_eur: Mapped[Decimal] = mapped_column(N, default=Decimal(0))  # órdenes maker abiertas
    start_equity: Mapped[Decimal] = mapped_column(N)
    peak_equity: Mapped[Decimal] = mapped_column(N)
    fees_total: Mapped[Decimal] = mapped_column(N, default=Decimal(0))
    realized_pnl: Mapped[Decimal] = mapped_column(N, default=Decimal(0))
    day: Mapped[str] = mapped_column(String(10), default="")
    day_start_equity: Mapped[Decimal] = mapped_column(N)
    benchmark_btc_price: Mapped[Decimal | None] = mapped_column(N, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Quote(Base):
    __tablename__ = "quotes"
    product_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    bid: Mapped[Decimal] = mapped_column(N)
    ask: Mapped[Decimal] = mapped_column(N)
    price: Mapped[Decimal] = mapped_column(N)
    source: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Product(Base):
    __tablename__ = "products"
    product_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    base: Mapped[str] = mapped_column(String(16))
    quote: Mapped[str] = mapped_column(String(16))
    base_increment: Mapped[Decimal] = mapped_column(N)
    quote_increment: Mapped[Decimal] = mapped_column(N)
    base_min_size: Mapped[Decimal] = mapped_column(N)
    quote_min_size: Mapped[Decimal] = mapped_column(N)
    tradable: Mapped[bool] = mapped_column(Boolean)
    volume_24h: Mapped[Decimal | None] = mapped_column(N, nullable=True)
    new_listing: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    equity: Mapped[Decimal] = mapped_column(N)
    cash: Mapped[Decimal] = mapped_column(N)
    invested: Mapped[Decimal] = mapped_column(N)
    btc_hold: Mapped[Decimal | None] = mapped_column(N, nullable=True)
    cash_hold: Mapped[Decimal] = mapped_column(N)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(8), default="INFO")  # INFO WARN CRITICAL
    actor: Mapped[str] = mapped_column(String(32))  # api | worker | engine | user
    event: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
