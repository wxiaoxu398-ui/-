from sqlmodel import create_engine


def test_usage_migration_preserves_old_message(tmp_path, monkeypatch):
    from app import db
    engine = create_engine(f'sqlite:///{(tmp_path / "old.db").as_posix()}')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE message (id INTEGER PRIMARY KEY, content TEXT NOT NULL)')
        connection.exec_driver_sql("INSERT INTO message VALUES (1, 'existing answer')")
    monkeypatch.setattr(db, 'engine', engine)
    db.init_db()
    db.init_db()
    with engine.connect() as connection:
        row = connection.exec_driver_sql('SELECT content, metrics_json FROM message WHERE id=1').one()
        assert row == ('existing answer', '{}')
    engine.dispose()
