"""Single-database schema. Response events are append-only; everything else is derived or frozen."""
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, MetaData, String,
                        Table, Text, UniqueConstraint, create_engine, event)

metadata = MetaData()


def now():
    return datetime.now(timezone.utc)


def _ts(name, nullable=True):
    return Column(name, DateTime(timezone=True), nullable=nullable)


scenes = Table(
    'scenes', metadata,
    Column('scene_id', String, primary_key=True),
    Column('split', String, nullable=False),
    Column('tone_key', String, nullable=False),
    Column('dynamic_range', String, nullable=False),
    Column('subject_tags', JSON, nullable=False),
    Column('chroma_level', String, nullable=False),
    Column('e2_class', String),
    _ts('imported_at', False),
)

stimuli = Table(
    'stimuli', metadata,
    Column('sha256', String, primary_key=True),  # of the master file as imported
    Column('scene_id', String, ForeignKey('scenes.scene_id'), nullable=False),
    Column('version_label', String, nullable=False),
    Column('source_type', String, nullable=False),
    Column('is_real', Boolean, nullable=False),
    Column('operator', String),
    Column('direction', String),
    Column('strength', Float),
    Column('region', String),
    Column('parent_sha', String, ForeignKey('stimuli.sha256')),
    Column('origin_dataset', String, nullable=False),
    Column('origin_id', String, nullable=False),
    Column('license_id', String, nullable=False),
    Column('regen_recipe', Text),
    Column('source_path', String, nullable=False),  # master location at import; re-verified by hash
    Column('source_profile', String, nullable=False),
    Column('width', Integer, nullable=False),
    Column('height', Integer, nullable=False),
    _ts('imported_at', False),
    UniqueConstraint('scene_id', 'version_label'),
)

derivatives = Table(
    'derivatives', metadata,
    Column('stimulus_sha', String, ForeignKey('stimuli.sha256'), primary_key=True),
    Column('css_long_edge', Integer, primary_key=True),
    Column('scale', String, primary_key=True),  # '1', '1.5', '2'
    Column('file_sha', String, nullable=False),
    Column('path', String, nullable=False),  # relative to <data>/media
    Column('width', Integer, nullable=False),
    Column('height', Integer, nullable=False),
)

segmentations = Table(
    'segmentations', metadata,
    Column('stimulus_sha', String, ForeignKey('stimuli.sha256'), primary_key=True),
    Column('label_map_path', String, nullable=False),
    Column('label_map_sha', String, nullable=False),
    Column('regions', JSON, nullable=False),
    _ts('imported_at', False),
)

experiments = Table(
    'experiments', metadata,
    Column('id', String, primary_key=True),
    Column('protocol_version', String, nullable=False),
    Column('template', String, nullable=False),
    Column('config', JSON, nullable=False),
    Column('config_hash', String),
    Column('code_version', String),
    Column('status', String, nullable=False),  # draft | frozen | live | closed
    _ts('created_at', False), _ts('frozen_at'), _ts('published_at'), _ts('closed_at'),
)

items = Table(
    'items', metadata,
    Column('id', Integer, primary_key=True),
    Column('experiment_id', String, ForeignKey('experiments.id'), nullable=False),
    Column('item_key', String, nullable=False),
    Column('kind', String, nullable=False),  # main | gold | practice
    Column('scene_id', String, ForeignKey('scenes.scene_id'), nullable=False),
    Column('stimuli', JSON, nullable=False),  # canonical order of stimulus shas
    Column('is_core', Boolean, nullable=False, default=False),
    Column('expected', String),  # gold / practice key, in canonical terms ('a' | 'b' | 'tie')
    UniqueConstraint('experiment_id', 'kind', 'item_key'),
)

invites = Table(
    'invites', metadata,
    Column('token', String, primary_key=True),
    Column('experiment_id', String, ForeignKey('experiments.id'), nullable=False),
    Column('tier', String, nullable=False),
    Column('arm', String),
    Column('max_uses', Integer, nullable=False),
    Column('uses', Integer, nullable=False, default=0),
    Column('note', String),
    _ts('created_at', False),
)

raters = Table(
    'raters', metadata,
    Column('id', Integer, primary_key=True),
    Column('anon_id', String, nullable=False, unique=True),
    Column('secret', String, nullable=False, unique=True),
    Column('profile', JSON),
    Column('external_source', String),
    Column('external_pid', String),
    _ts('created_at', False),
)

enrollments = Table(
    'enrollments', metadata,
    Column('id', Integer, primary_key=True),
    Column('rater_id', Integer, ForeignKey('raters.id'), nullable=False),
    Column('experiment_id', String, ForeignKey('experiments.id'), nullable=False),
    Column('tier', String, nullable=False),
    Column('arm', String, nullable=False),
    Column('invite_token', String, ForeignKey('invites.token')),
    Column('consent_version', String),
    Column('display_passed', Boolean, nullable=False, default=False),
    Column('practice_done', Boolean, nullable=False, default=False),
    Column('completion_code', String),
    _ts('created_at', False),
    UniqueConstraint('rater_id', 'experiment_id'),
)

display_checks = Table(
    'display_checks', metadata,
    Column('id', Integer, primary_key=True),
    Column('enrollment_id', Integer, ForeignKey('enrollments.id'), nullable=False),
    Column('session_id', String, ForeignKey('sessions.id')),
    Column('payload', JSON, nullable=False),
    Column('passed', Boolean, nullable=False),
    Column('reasons', JSON, nullable=False),
    _ts('created_at', False),
)

sessions = Table(
    'sessions', metadata,
    Column('id', String, primary_key=True),
    Column('enrollment_id', Integer, ForeignKey('enrollments.id'), nullable=False),
    Column('user_agent', String),
    _ts('started_at', False), _ts('last_seen', False),
)

blocks = Table(
    'blocks', metadata,
    Column('id', Integer, primary_key=True),
    Column('enrollment_id', Integer, ForeignKey('enrollments.id'), nullable=False),
    Column('session_id', String, ForeignKey('sessions.id')),
    Column('seq', Integer, nullable=False),
    Column('kind', String, nullable=False),  # practice | main
    Column('seed', Integer, nullable=False),
    Column('plan', JSON, nullable=False),  # planner diagnostics, for audit
    _ts('planned_at', False), _ts('completed_at'),
    UniqueConstraint('enrollment_id', 'seq'),
)

assignments = Table(
    'assignments', metadata,
    Column('id', Integer, primary_key=True),
    Column('block_id', Integer, ForeignKey('blocks.id'), nullable=False),
    Column('enrollment_id', Integer, ForeignKey('enrollments.id'), nullable=False),
    Column('item_id', Integer, ForeignKey('items.id'), nullable=False),
    Column('position', Integer, nullable=False),
    Column('role', String, nullable=False),  # normal | hidden_repeat | swap | gold | practice | followup
    Column('repeat_of', Integer, ForeignKey('assignments.id')),
    Column('layout', JSON, nullable=False),  # displayed order of stimulus shas (left→right / grid order)
    Column('token', String, nullable=False, unique=True),  # opaque id used in image URLs
    Column('status', String, nullable=False),  # reserved | answered | expired
    _ts('reserved_until'),
    UniqueConstraint('block_id', 'position'),
)

events = Table(
    'events', metadata,
    Column('id', Integer, primary_key=True),
    Column('enrollment_id', Integer, ForeignKey('enrollments.id'), nullable=False),
    Column('session_id', String, ForeignKey('sessions.id')),
    Column('assignment_id', Integer, ForeignKey('assignments.id')),
    Column('type', String, nullable=False),
    Column('payload', JSON, nullable=False),
    Column('rt_ms', Integer),
    Column('client_ts', String),
    _ts('server_ts', False),
)


def database_path(data_dir):
    return Path(data_dir).resolve() / 'easylabel.db'


def open_engine(data_dir, create=True):
    data_dir = Path(data_dir).resolve()
    if create:
        (data_dir / 'media').mkdir(parents=True, exist_ok=True)
    elif not database_path(data_dir).exists():
        raise FileNotFoundError(f'数据目录中没有 easylabel.db：{data_dir}')
    engine = create_engine(f'sqlite:///{database_path(data_dir)}', future=True)

    @event.listens_for(engine, 'connect')
    def _pragmas(dbapi, _):
        cur = dbapi.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA foreign_keys=ON')
        cur.execute('PRAGMA busy_timeout=5000')
        cur.close()

    metadata.create_all(engine)
    return engine
