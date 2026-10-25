import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()

database_url = os.getenv("DATABASE_URL")

engine = create_engine(database_url)

sessionmaker = sessionmaker(
    bind = engine,
    autoflush=False,
    expire_on_commit= False,

)

class Base(DeclarativeBase):
    pass

def get_db():
    db = sessionmaker()

    try:
        yield db

    finally:
        db.close()
