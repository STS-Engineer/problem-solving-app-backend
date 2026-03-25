from sqlalchemy import Column, Integer, String
from app.db.base import Base


class AvoMember(Base):
    __tablename__ = "avomembers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    name = Column(String(200), nullable=False)
    email = Column(String(255), nullable=True)

    department = Column(String(100), nullable=True)
    role = Column(String(100), nullable=True)

    city = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)
    office = Column(String(100), nullable=True)
