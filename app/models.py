"""Modelli SQLAlchemy che mappano le tabelle create da docs/schema.sql.

docs/schema.sql resta la fonte di verità per la DDL (vedi app/db.py). Questi
modelli non generano schema (niente Base.metadata.create_all): servono solo
per l'accesso ORM, e vanno tenuti manualmente in sync con schema.sql quando
quest'ultimo cambia.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import String, TypeDecorator

from app import crypto


class Base(DeclarativeBase):
    pass


class EncryptedString(TypeDecorator):
    """Cifra/decifra trasparentemente i segreti salvati a riposo (api_token,
    password) — vedi app/crypto.py e docs/schema.sql."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return crypto.encrypt(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return crypto.decrypt(value)


# ============ CONFIGURAZIONE ============


class Disk(Base):
    __tablename__ = "disk"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(nullable=False)
    root_path: Mapped[str] = mapped_column(nullable=False, unique=True)
    st_dev: Mapped[int | None]
    torrents_rel_path: Mapped[str | None]
    torrent_client_root_path: Mapped[str | None]
    created_at: Mapped[datetime | None] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))

    media_paths: Mapped[list["MediaPath"]] = relationship(
        back_populates="disk", cascade="all, delete-orphan"
    )


class MediaPath(Base):
    __tablename__ = "media_path"
    __table_args__ = (
        UniqueConstraint("disk_id", "relative_path"),
        CheckConstraint("content_type IN ('movie','tv')", name="ck_media_path_content_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    disk_id: Mapped[int] = mapped_column(ForeignKey("disk.id", ondelete="CASCADE"), nullable=False)
    relative_path: Mapped[str] = mapped_column(nullable=False)
    content_type: Mapped[str] = mapped_column(nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("1"))
    new_torrent_rel_path: Mapped[str | None]

    disk: Mapped["Disk"] = relationship(back_populates="media_paths")
    media_items: Mapped[list["MediaItem"]] = relationship(
        back_populates="media_path", cascade="all, delete-orphan"
    )

    @property
    def effective_new_torrent_rel_path(self) -> str | None:
        """Cartella dove va creato un NUOVO hardlink per questa libreria (e
        il save_path da comunicare al client) se configurata, altrimenti
        quella del disco — vedi app/executor.py. Riguarda SOLO dove
        posizionare cose nuove: la ricerca "già in seeding" resta sempre
        sull'intera disk.torrents_rel_path (mai ristretta a questa
        sottocartella), perché un client può organizzare i completed in
        più sottocartelle che vanno comunque scansionate tutte."""
        return self.new_torrent_rel_path or self.disk.torrents_rel_path


class Tracker(Base):
    __tablename__ = "tracker"
    __table_args__ = (
        CheckConstraint("history_mode IN ('api','scrape','unsupported')", name="ck_tracker_history_mode"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(nullable=False)
    adapter_type: Mapped[str] = mapped_column(nullable=False)
    base_url: Mapped[str] = mapped_column(nullable=False)
    api_token: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    history_mode: Mapped[str] = mapped_column(nullable=False, server_default=text("'unsupported'"))
    history_session_cookie: Mapped[str | None] = mapped_column(EncryptedString)
    rate_limit_per_min: Mapped[int | None] = mapped_column(server_default=text("30"))
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("1"))

    candidates: Mapped[list["Candidate"]] = relationship(back_populates="tracker")


class TorrentClient(Base):
    __tablename__ = "torrent_client"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(nullable=False)
    adapter_type: Mapped[str] = mapped_column(nullable=False)
    base_url: Mapped[str] = mapped_column(nullable=False)
    username: Mapped[str | None]
    password: Mapped[str | None] = mapped_column(EncryptedString)
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("1"))


class AppSettings(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(nullable=False)


# ============ DOMINIO ============


class MediaItem(Base):
    __tablename__ = "media_item"
    __table_args__ = (UniqueConstraint("media_path_id", "file_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    media_path_id: Mapped[int] = mapped_column(
        ForeignKey("media_path.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(nullable=False)
    inode: Mapped[int | None]
    st_dev: Mapped[int | None]
    nlink: Mapped[int | None]
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    mediainfo_unique_id: Mapped[str | None]
    tmdb_id: Mapped[int | None]
    season_number: Mapped[int | None]
    episode_number: Mapped[int | None]
    resolver_source: Mapped[str | None]
    last_scanned_at: Mapped[datetime | None]

    media_path: Mapped["MediaPath"] = relationship(back_populates="media_items")
    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="media_item", cascade="all, delete-orphan"
    )


class Candidate(Base):
    __tablename__ = "candidate"
    __table_args__ = (
        CheckConstraint("source IN ('history','catalog_search')", name="ck_candidate_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), nullable=False
    )
    tracker_id: Mapped[int] = mapped_column(ForeignKey("tracker.id"), nullable=False)
    torrent_id_remote: Mapped[str] = mapped_column(nullable=False)
    info_hash: Mapped[str | None]
    name: Mapped[str] = mapped_column(nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    file_list_json: Mapped[str | None]
    folder: Mapped[str | None]
    download_link: Mapped[str | None]
    source: Mapped[str] = mapped_column(nullable=False)
    size_match: Mapped[bool | None]
    mediainfo_match: Mapped[bool | None]
    confidence: Mapped[float] = mapped_column(nullable=False)
    ambiguity_reason: Mapped[str | None]
    created_at: Mapped[datetime | None] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))

    media_item: Mapped["MediaItem"] = relationship(back_populates="candidates")
    tracker: Mapped["Tracker"] = relationship(back_populates="candidates")
    # cascade esplicito lato ORM: match_review.candidate_id e
    # seed_job.candidate_id sono NOT NULL senza ON DELETE CASCADE nello
    # schema (docs/schema.sql, non alterabile su un DB SQLite già creato).
    # Senza questo cascade, eliminare un Candidate (a cascata da MediaItem/
    # MediaPath/Disk) fa fallire il commit con un IntegrityError perché
    # SQLAlchemy prova a mettere a NULL candidate_id sui figli invece di
    # cancellarli — era la causa per cui eliminare un disco dalla UI
    # falliva sempre non appena aveva almeno un match_review/seed_job.
    match_reviews: Mapped[list["MatchReview"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    seed_jobs: Mapped[list["SeedJob"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class MatchReview(Base):
    __tablename__ = "match_review"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','auto_approved')",
            name="ck_match_review_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate.id"), nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, server_default=text("'pending'"))
    decided_by: Mapped[str | None]
    decided_at: Mapped[datetime | None]

    candidate: Mapped["Candidate"] = relationship(back_populates="match_reviews")


class SeedJob(Base):
    __tablename__ = "seed_job"
    __table_args__ = (
        CheckConstraint("recheck_status IN ('pending','ok','failed')", name="ck_seed_job_recheck_status"),
        CheckConstraint(
            "final_status IN ('in_progress','seeding','failed','rolled_back')",
            name="ck_seed_job_final_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate.id"), nullable=False)
    hardlink_path: Mapped[str | None]
    hardlink_created_at: Mapped[datetime | None]
    torrent_added_at: Mapped[datetime | None]
    info_hash: Mapped[str | None]
    recheck_status: Mapped[str | None]
    final_status: Mapped[str] = mapped_column(nullable=False, server_default=text("'in_progress'"))
    error_message: Mapped[str | None]

    candidate: Mapped["Candidate"] = relationship(back_populates="seed_jobs")


class RunLog(Base):
    __tablename__ = "run_log"
    __table_args__ = (
        CheckConstraint("run_type IN ('scheduled','manual','bulk_import')", name="ck_run_log_run_type"),
        CheckConstraint(
            "current_phase IN ('scanning','matching','executing')", name="ck_run_log_current_phase"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_type: Mapped[str] = mapped_column(nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    finished_at: Mapped[datetime | None]
    current_phase: Mapped[str | None]
    phase_total: Mapped[int | None]
    phase_done: Mapped[int | None]
    items_total: Mapped[int | None]
    items_scanned: Mapped[int | None] = mapped_column(server_default=text("0"))
    matches_found: Mapped[int | None] = mapped_column(server_default=text("0"))
    auto_seeded: Mapped[int | None] = mapped_column(server_default=text("0"))
    pending_review: Mapped[int | None] = mapped_column(server_default=text("0"))
    errors: Mapped[int | None] = mapped_column(server_default=text("0"))
