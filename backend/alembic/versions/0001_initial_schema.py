"""initial_schema_users_jobs_audit_logs

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-11-17 20:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

user_role_enum = postgresql.ENUM(
    "OPERATOR",
    "REVIEWER",
    "DPO_AUDITOR",
    "ADMIN",
    name="user_role_enum",
    create_type=False,
)

job_status_enum = postgresql.ENUM(
    "RECEIVED",
    "ANALYZING",
    "OCR_RUNNING",
    "PARTIAL_REVIEW_READY",
    "AWAITING_REVIEW",
    "REDACTING",
    "VALIDATING",
    "COMPLETED",
    "FAILED",
    name="job_status_enum",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    user_role_enum.create(bind, checkfirst=True)
    job_status_enum.create(bind, checkfirst=True)

    # 1. Tabela users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("keycloak_sub", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=150), nullable=False),
        sa.Column("organizational_unit", sa.String(length=120), nullable=True),
        sa.Column("role", user_role_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_keycloak_sub"), "users", ["keycloak_sub"], unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_index(op.f("ix_users_organizational_unit"), "users", ["organizational_unit"], unique=False)

    # 2. Tabela processing_jobs
    op.create_table(
        "processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", job_status_enum, nullable=False),
        sa.Column("original_file_sha256", sa.String(length=64), nullable=False),
        sa.Column("redacted_file_sha256", sa.String(length=64), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_processing_jobs_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_jobs")),
    )
    op.create_index(op.f("ix_processing_jobs_user_id"), "processing_jobs", ["user_id"], unique=False)
    op.create_index(op.f("ix_processing_jobs_status"), "processing_jobs", ["status"], unique=False)
    op.create_index(
        op.f("ix_processing_jobs_original_file_sha256"),
        "processing_jobs",
        ["original_file_sha256"],
        unique=False,
    )
    op.create_index(
        op.f("ix_processing_jobs_redacted_file_sha256"),
        "processing_jobs",
        ["redacted_file_sha256"],
        unique=False,
    )
    op.create_index(
        op.f("ix_processing_jobs_celery_task_id"),
        "processing_jobs",
        ["celery_task_id"],
        unique=False,
    )
    op.create_index(op.f("ix_processing_jobs_expires_at"), "processing_jobs", ["expires_at"], unique=False)

    # 3. Tabela audit_logs (Zero-PII: somente contadores por categoria, hashes SHA-256, páginas e timestamp)
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("organizational_unit", sa.String(length=120), nullable=True),
        sa.Column("original_document_sha256", sa.String(length=64), nullable=False),
        sa.Column("redacted_document_sha256", sa.String(length=64), nullable=False),
        sa.Column("count_personal_common", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("count_personal_sensitive", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("count_financial_banking", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("count_minor_or_dependent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_redactions_applied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "category_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "affected_pages",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'::integer[]"),
        ),
        sa.Column("total_pages", sa.Integer(), nullable=False),
        sa.Column("human_review_confirmed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "anonymization_disclaimer_acknowledged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("metadata_sanitized", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "count_personal_common >= 0",
            name=op.f("ck_audit_logs_non_negative_personal_common"),
        ),
        sa.CheckConstraint(
            "count_personal_sensitive >= 0",
            name=op.f("ck_audit_logs_non_negative_personal_sensitive"),
        ),
        sa.CheckConstraint(
            "count_financial_banking >= 0",
            name=op.f("ck_audit_logs_non_negative_financial_banking"),
        ),
        sa.CheckConstraint(
            "count_minor_or_dependent >= 0",
            name=op.f("ck_audit_logs_non_negative_minor_or_dependent"),
        ),
        sa.CheckConstraint(
            "total_redactions_applied >= 0",
            name=op.f("ck_audit_logs_non_negative_total_redactions"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_audit_logs_job_id_processing_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_job_id"), "audit_logs", ["job_id"], unique=True)
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_audit_logs_organizational_unit"),
        "audit_logs",
        ["organizational_unit"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_logs_original_document_sha256"),
        "audit_logs",
        ["original_document_sha256"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_logs_redacted_document_sha256"),
        "audit_logs",
        ["redacted_document_sha256"],
        unique=False,
    )
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_redacted_document_sha256"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_original_document_sha256"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_organizational_unit"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_user_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_job_id"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_processing_jobs_expires_at"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_celery_task_id"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_redacted_file_sha256"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_original_file_sha256"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_status"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_user_id"), table_name="processing_jobs")
    op.drop_table("processing_jobs")

    op.drop_index(op.f("ix_users_organizational_unit"), table_name="users")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_keycloak_sub"), table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    job_status_enum.drop(bind, checkfirst=True)
    user_role_enum.drop(bind, checkfirst=True)
