import os
import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

# Database URL comes from Render environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# SQLAlchemy setup
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(14), unique=True, index=True, nullable=False)  # XXXX-XXXX-XXXX
    email = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    active = Column(Boolean, nullable=False, default=True)

    devices = relationship(
        "LicenseDevice",
        back_populates="license",
        cascade="all, delete-orphan",
    )


class LicenseDevice(Base):
    __tablename__ = "license_devices"
    __table_args__ = (
        UniqueConstraint("license_id", "machine_id", name="uq_license_machine"),
    )

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False)
    machine_id = Column(String(128), nullable=False, index=True)
    first_seen = Column(DateTime(timezone=True), nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=False)

    license = relationship("License", back_populates="devices")


ALPHABET = string.ascii_uppercase + string.digits


def generate_license_code() -> str:
    """
    Generate a code like XXXX-XXXX-XXXX using A–Z and 0–9.
    """
    def block() -> str:
        return "".join(secrets.choice(ALPHABET) for _ in range(4))

    return f"{block()}-{block()}-{block()}"


def init_db() -> None:
    """
    Create tables if they do not exist.
    """
    Base.metadata.create_all(bind=engine)


def create_license(db: Session, email: str) -> License:
    """
    Create a new 1-year license for this email.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=365)

    # Try a few times in case of a very rare collision
    for _ in range(10):
        code = generate_license_code()
        existing = db.query(License).filter(License.code == code).first()
        if existing is None:
            break
    else:
        raise RuntimeError("Could not generate a unique license code")

    lic = License(
        code=code,
        email=email,
        created_at=now,
        expires_at=expires_at,
        active=True,
    )

    db.add(lic)
    db.commit()
    db.refresh(lic)
    return lic
