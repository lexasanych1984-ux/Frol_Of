-- Схема D1 для облачного буфера сигналов (см. cloud/worker.js).
-- Применить: wrangler d1 execute bybit-signals --file=cloud/schema.sql --remote
CREATE TABLE IF NOT EXISTS signals (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,  -- монотонный курсор для GET /pull?after=<id>
  ts    INTEGER NOT NULL,                   -- epoch (сек) приёма ≈ время срабатывания алерта
  body  TEXT    NOT NULL,                   -- сырой alert_message (что исполняет бот)
  hash  TEXT    NOT NULL                    -- sha256(body + '|' + floor(ts/60)) — дедуп ретраев
);
CREATE INDEX IF NOT EXISTS idx_signals_hash ON signals(hash);
CREATE INDEX IF NOT EXISTS idx_signals_ts   ON signals(ts);
