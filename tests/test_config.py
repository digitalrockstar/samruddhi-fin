from app.config import Settings

def test_neon_url_translation():
    s=Settings(database_url="postgresql://u:p@h/db?sslmode=require&channel_binding=require")
    assert s.db_url().startswith("postgresql+asyncpg://")
    assert "sslmode" not in s.db_url()
    assert "channel_binding" not in s.db_url()
