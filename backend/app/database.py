from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings

class Base(DeclarativeBase): pass

def make_engine(url):
    return create_engine(url, connect_args={'check_same_thread':False} if url.startswith('sqlite') else {}, pool_pre_ping=True)

engine=make_engine(get_settings().database_url)
SessionLocal=sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def db_session():
    with SessionLocal() as db: yield db
