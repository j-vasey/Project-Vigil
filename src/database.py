import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

# Find absolute path of the project root directory
import sys
appdata = os.environ.get("APPDATA")
if appdata:
    BASE_DIR = os.path.join(appdata, "ProjectVigil")
else:
    BASE_DIR = os.path.join(os.path.expanduser("~"), ".project_vigil")

os.makedirs(BASE_DIR, exist_ok=True)
DB_PATH = os.path.join(BASE_DIR, "project_vigil.db")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")

# Setup engine with check_same_thread=False to support multi-threading in FastAPI with SQLite
engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Conversation(Base):
    """
    Tracks message history between the bot/agent and users.
    """
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    channel = Column(String, index=True, nullable=False)       # Platform name e.g., 'telegram', 'mock'
    user_id = Column(String, index=True, nullable=False)       # Platform-specific unique user/chat identifier
    sender_type = Column(String, nullable=False)              # 'user' or 'bot'
    text = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class Configuration(Base):
    """
    Key-Value configuration store for runtime configurations like LLM endpoints, credentials, and DND settings.
    """
    __tablename__ = "configurations"

    key = Column(String, primary_key=True, index=True)
    value = Column(Text, nullable=False)


class ProactivityLog(Base):
    """
    Audit log tracking autonomous proactive outreach triggers, timings, and results.
    """
    __tablename__ = "proactivity_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    execution_time = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    reason_code = Column(String, index=True, nullable=False)   # E.g. 'morning_brief', 'evening_summary'
    message_dispatched = Column(Text, nullable=True)          # Content sent, or NULL if blocked by DND


class ActiveMemory(Base):
    """
    Stores persistent, evolutionary context facts about the user or their habits.
    """
    __tablename__ = "active_memories"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    fact = Column(Text, nullable=False)
    category = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentJobState(Base):
    """
    Tracks and checkpoint background agent tasks plans and findings.
    """
    __tablename__ = "agent_job_state"

    job_id = Column(String, primary_key=True, index=True)
    status = Column(String, index=True, nullable=False)        # 'running', 'completed', 'failed'
    plan = Column(Text, nullable=True)                         # JSON string representation of plans
    findings = Column(Text, nullable=True)                     # JSON string representation of sub-findings
    artifacts = Column(Text, nullable=True)                    # JSON string list of artifact file paths
    step_count = Column(Integer, default=0)
    last_update = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserTrendLog(Base):
    """
    Tracks meta-metrics on every user interaction for behavior and sentiment analysis.
    """
    __tablename__ = "user_trend_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    stress_level = Column(String, index=True, nullable=False)  # 'low', 'medium', 'high'
    topics = Column(String, nullable=True)                     # comma-separated topics
    user_message = Column(Text, nullable=True)


def init_db():
    """
    Creates tables if they do not exist.
    """
    import logging
    logger = logging.getLogger("project_vigil.database")
    
    Base.metadata.create_all(bind=engine)
    
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            cursor = conn.execute(text("PRAGMA table_info(agent_job_state)"))
            columns = [row[1] for row in cursor.fetchall()]
            if "artifacts" not in columns:
                conn.execute(text("ALTER TABLE agent_job_state ADD COLUMN artifacts TEXT"))
                logger.info("[Database] Successfully added 'artifacts' column to agent_job_state.")
            
            # m365_redirect_uri is now auto-derived from url_root at runtime; no hardcoded default needed
    except Exception as e:
        logger.error(f"[Database] Error checking/migrating agent_job_state table: {e}")
