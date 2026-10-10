from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine
from .config import settings

settings.db_path.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f'sqlite:///{settings.db_path.as_posix()}', connect_args={'check_same_thread': False, 'timeout': 30})


@event.listens_for(engine, 'connect')
def configure_sqlite(connection, _):
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA foreign_keys=ON')


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
