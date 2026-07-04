from datetime import datetime, timezone
from typing import List, Dict, Optional
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import Session
from src.database import Conversation, Configuration, ProactivityLog, ActiveMemory, AgentJobState, UserTrendLog, ConversationSummary

class MessageRepository:
    """
    Handles database operations for Project Vigil, providing a clean abstraction 
    over the database sessions.
    """

    def __init__(self, db: Session):
        self.db = db

    # --- Conversation history ---
    def save_message(
        self, 
        channel: str, 
        user_id: str, 
        sender_type: str, 
        text: str, 
        timestamp: Optional[datetime] = None
    ) -> Conversation:
        """
        Saves a single conversation record (incoming from user or outgoing from bot).
        """
        db_msg = Conversation(
            channel=channel.lower(),
            user_id=user_id,
            sender_type=sender_type.lower(),
            text=text,
            timestamp=timestamp or datetime.now(timezone.utc)
        )
        self.db.add(db_msg)
        self.db.commit()
        self.db.refresh(db_msg)
        return db_msg

    def get_sliding_window_history(
        self, channel: str, user_id: str, limit: int = 10, max_tokens: int = 3000
    ) -> List[Conversation]:
        """
        Retrieves the last N messages for the user globally across all configured channels,
        ordered chronologically (ascending).
        Applies a token-budget guard (estimating 1 token ≈ 4 chars) so a single large LLM
        response cannot silently overflow the model's context window.
        """
        tg_id = self.get_config("telegram_user_id", "")
        ds_id = self.get_config("discord_user_id", "")
        twilio_num = self.get_config("twilio_number", "")
        
        # Build list of active platform-recipient filters
        conditions = [
            and_(Conversation.channel == channel.lower(), Conversation.user_id == user_id)
        ]
        
        if tg_id:
            conditions.append(and_(Conversation.channel == "telegram", Conversation.user_id == tg_id))
        if ds_id:
            conditions.append(and_(Conversation.channel == "discord", Conversation.user_id == ds_id))
        if twilio_num:
            conditions.append(and_(Conversation.channel == "twilio", Conversation.user_id == twilio_num))
            conditions.append(and_(Conversation.channel == "whatsapp", Conversation.user_id == twilio_num))
            if not twilio_num.startswith("whatsapp:"):
                conditions.append(and_(Conversation.channel == "whatsapp", Conversation.user_id == f"whatsapp:{twilio_num}"))
            
        if channel.lower() == "mock":
            conditions.append(and_(Conversation.channel == "mock", Conversation.user_id == user_id))
            
        history = self.db.query(Conversation)\
            .filter(or_(*conditions))\
            .order_by(Conversation.timestamp.desc())\
            .limit(limit)\
            .all()
        history = history[::-1]  # chronological order

        # Token budget guard: walk newest→oldest, include until budget exhausted
        if max_tokens and max_tokens > 0:
            budget = max_tokens
            kept = []
            for msg in reversed(history):
                estimated = max(1, len(msg.text) // 4)
                if budget - estimated < 0:
                    break
                budget -= estimated
                kept.append(msg)
            history = list(reversed(kept))

        return history

    def get_latest_conversation_summary(self, channel: str, user_id: str) -> Optional[ConversationSummary]:
        """Gets the most recent conversation summary for this user/channel."""
        return self.db.query(ConversationSummary)\
            .filter(ConversationSummary.channel == channel.lower(), ConversationSummary.user_id == user_id)\
            .order_by(ConversationSummary.covering_to.desc())\
            .first()

    def get_unsummarised_window(self, channel: str, user_id: str, batch_size: int = 20) -> List[Conversation]:
        """
        Gets the oldest batch of unsummarised messages if there are enough.
        """
        last_summary = self.get_latest_conversation_summary(channel, user_id)
        last_id = last_summary.covering_to if last_summary else 0
        
        msgs = self.db.query(Conversation)\
            .filter(Conversation.channel == channel.lower(), Conversation.user_id == user_id, Conversation.id > last_id)\
            .order_by(Conversation.id.asc())\
            .limit(batch_size)\
            .all()
            
        if len(msgs) >= batch_size:
            return msgs
        return []
        
    def save_conversation_summary(self, channel: str, user_id: str, summary: str, from_id: int, to_id: int):
        new_summary = ConversationSummary(
            channel=channel.lower(),
            user_id=user_id,
            summary_text=summary,
            covering_from=from_id,
            covering_to=to_id
        )
        self.db.add(new_summary)
        self.db.commit()

    # --- Configurations ---
    def get_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Retrieves a configuration parameter value by its key. Decrypts tokens if encrypted.
        """
        config = self.db.query(Configuration).filter(Configuration.key == key).first()
        if not config:
            return default
        
        if key in ("telegram_token", "discord_token"):
            from src.security import decrypt_token
            return decrypt_token(config.value)
            
        return config.value

    def get_all_configs(self) -> Dict[str, str]:
        """
        Returns all registered configuration keys and values as a dictionary.
        Masks secret tokens (e.g., telegram_token, discord_token) for client-facing API endpoints.
        """
        configs = self.db.query(Configuration).all()
        result = {}
        for c in configs:
            if c.key in ("telegram_token", "discord_token"):
                if c.value and c.value.strip():
                    result[c.key] = "********"
                else:
                    result[c.key] = ""
            else:
                result[c.key] = c.value
        return result

    def set_config(self, key: str, value: str) -> Configuration:
        """
        Inserts or updates a configuration key-value pair. Encrypts secret tokens.
        Skips updating the key if the incoming payload is masked (e.g. "********").
        """
        config = self.db.query(Configuration).filter(Configuration.key == key).first()
        
        if key in ("telegram_token", "discord_token"):
            val_str = str(value)
            if val_str == "********":
                # Avoid overwriting with mask; return existing config or set to empty if none exists
                if config:
                    return config
                value = ""
            else:
                from src.security import encrypt_token
                value = encrypt_token(val_str)

        if config:
            config.value = str(value)
        else:
            config = Configuration(key=key, value=str(value))
            self.db.add(config)
            
        self.db.commit()
        self.db.refresh(config)
        return config

    # --- Proactivity logs ---
    def log_proactivity(
        self, 
        reason_code: str, 
        message_dispatched: Optional[str] = None, 
        execution_time: Optional[datetime] = None
    ) -> ProactivityLog:
        """
        Creates an audit entry logging a proactive outreach attempt.
        """
        log_entry = ProactivityLog(
            reason_code=reason_code,
            message_dispatched=message_dispatched,
            execution_time=execution_time or datetime.now(timezone.utc)
        )
        self.db.add(log_entry)
        self.db.commit()
        self.db.refresh(log_entry)
        return log_entry

    def get_recent_proactivity_logs(self, limit: int = 50) -> List[ProactivityLog]:
        """
        Fetches the latest proactivity logs.
        """
        return self.db.query(ProactivityLog)\
            .order_by(ProactivityLog.execution_time.desc())\
            .limit(limit)\
            .all()

    # --- Active Memories CRUD ---
    def search_memories(self, query: str = "") -> List[ActiveMemory]:
        """
        Query and filter stored active memories.
        """
        q = self.db.query(ActiveMemory)
        if query:
            pattern = f"%{query}%"
            q = q.filter(ActiveMemory.fact.like(pattern) | ActiveMemory.category.like(pattern))
        return q.order_by(ActiveMemory.timestamp.desc()).all()

    def save_memory(self, fact: str, category: str, memory_id: Optional[int] = None) -> ActiveMemory:
        """
        Save or update an active memory fact category payload.
        """
        if memory_id:
            memory = self.db.query(ActiveMemory).filter(ActiveMemory.id == memory_id).first()
            if memory:
                memory.fact = fact
                memory.category = category
                memory.timestamp = datetime.now(timezone.utc)
                self.db.commit()
                self.db.refresh(memory)
                return memory
                
        # Create new
        memory = ActiveMemory(fact=fact, category=category, timestamp=datetime.now(timezone.utc))
        self.db.add(memory)
        self.db.commit()
        self.db.refresh(memory)
        return memory

    def delete_memory(self, memory_id: int) -> bool:
        """
        Deletes a stored active memory fact.
        """
        memory = self.db.query(ActiveMemory).filter(ActiveMemory.id == memory_id).first()
        if memory:
            self.db.delete(memory)
            self.db.commit()
            return True
        return False

    # --- Agent Job State Checkpointing ---
    def save_agent_job_state(self, job_id: str, status: str, plan_json: str, findings_json: str, step_count: int, artifacts_json: str = None) -> AgentJobState:
        """
        Durable checkpointing of background agent execution state.
        """
        job = self.db.query(AgentJobState).filter(AgentJobState.job_id == job_id).first()
        if not job:
            job = AgentJobState(job_id=job_id)
            self.db.add(job)
        job.status = status
        job.plan = plan_json
        job.findings = findings_json
        job.step_count = step_count
        if artifacts_json is not None:
            job.artifacts = artifacts_json
        job.last_update = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(job)
        return job

    def get_agent_job_state(self, job_id: str) -> Optional[AgentJobState]:
        """
        Retrieves serialized agent state for checkpointing.
        """
        return self.db.query(AgentJobState).filter(AgentJobState.job_id == job_id).first()

    # --- Sentiment / User Trend Logs ---
    def log_user_trend(self, stress_level: str, topics: str, user_message: str) -> UserTrendLog:
        """
        Log user meta-metrics for behavioral analysis.
        """
        log = UserTrendLog(
            timestamp=datetime.now(timezone.utc),
            stress_level=stress_level,
            topics=topics,
            user_message=user_message
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log
