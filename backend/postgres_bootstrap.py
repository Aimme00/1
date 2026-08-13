from __future__ import annotations

from pathlib import Path

from env_settings import env_int


def ensure_postgres_schema(database_url: str) -> None:
    """幂等初始化演示业务表和 Vercel 持久化表。"""
    import psycopg

    project_root = Path(__file__).resolve().parents[1]
    business_sql = (project_root / "sql" / "create_trade_demo_postgres.sql").read_text(
        encoding="utf-8"
    )
    internal_sql = """
    CREATE TABLE IF NOT EXISTS _askdata_messages (
        id BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        message_type TEXT NOT NULL DEFAULT 'text',
        payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        token_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_askdata_messages_session
      ON _askdata_messages(user_id, session_id, id);

    CREATE TABLE IF NOT EXISTS _askdata_summaries (
        id BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        content TEXT NOT NULL,
        through_message_id BIGINT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, session_id, version)
    );

    CREATE TABLE IF NOT EXISTS _askdata_long_term_memories (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        summary TEXT NOT NULL,
        content_json JSONB NOT NULL,
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        embedding BYTEA NOT NULL,
        embedding_dimensions INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS _askdata_saved_analyses (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        title TEXT NOT NULL,
        query TEXT NOT NULL,
        result_json JSONB NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(user_id, run_id)
    );

    CREATE TABLE IF NOT EXISTS _askdata_dashboards (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS _askdata_dashboard_cards (
        id TEXT PRIMARY KEY,
        dashboard_id TEXT NOT NULL REFERENCES _askdata_dashboards(id) ON DELETE CASCADE,
        analysis_id TEXT NOT NULL REFERENCES _askdata_saved_analyses(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(dashboard_id, analysis_id)
    );

    CREATE TABLE IF NOT EXISTS _askdata_quota (
        subject_hash TEXT PRIMARY KEY,
        query_count INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS _askdata_runs (
        run_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        query TEXT NOT NULL,
        status TEXT NOT NULL,
        result_json JSONB,
        error_json JSONB,
        events_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_askdata_runs_user
      ON _askdata_runs(user_id, created_at DESC);
    """
    with psycopg.connect(
        database_url,
        connect_timeout=env_int(
            "ASKDATA_POSTGRES_CONNECT_TIMEOUT", 10, minimum=1, maximum=30
        ),
    ) as connection:
        # Vercel 可能同时冷启动多个实例。事务级 advisory lock 可避免多个实例
        # 在同一时刻重复执行建表语句，连接提交或回滚后会自动释放。
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (2026081201,))
        for statement in (business_sql, internal_sql):
            with connection.cursor() as cursor:
                cursor.execute(statement, prepare=False)
