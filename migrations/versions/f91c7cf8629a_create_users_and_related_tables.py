"""create users and related tables

Revision ID: f91c7cf8629a
Revises: e5e8a142664e
Create Date: 2025-10-24 09:43:38.839856
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f91c7cf8629a"
down_revision = "e5e8a142664e"
branch_labels = None
depends_on = None


def upgrade():
    # ---------------------- USERS ----------------------
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default=sa.text("'admin'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    # índice funcional único para case-insensitive
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(
            "uq_users_username_lower",
            [sa.text("lower(username)")],
            unique=True,
            postgresql_using="btree",
        )

    # ---------------------- EVENT_CHECKINS ----------------------
    with op.batch_alter_table("event_checkins", schema=None) as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=120), nullable=True))

        # INTEGER -> BIGINT
        batch_op.alter_column(
            "id",
            existing_type=sa.INTEGER(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )

        # birth_date já é VARCHAR(10); apenas garantir nulabilidade
        batch_op.alter_column(
            "birth_date",
            existing_type=sa.VARCHAR(length=10),
            nullable=True,
        )

        # TIMESTAMP -> TIMESTAMPTZ
        batch_op.alter_column(
            "created_at",
            existing_type=postgresql.TIMESTAMP(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=postgresql.TIMESTAMP(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
        )

        # reindexar e unique correto
        batch_op.drop_index("ix_event_checkins_event_checkins_cpf")
        batch_op.drop_index("ix_event_checkins_event_checkins_event_date")
        batch_op.drop_constraint("uq_event_date_cpf", type_="unique")

        batch_op.create_index(batch_op.f("ix_event_checkins_cpf"), ["cpf"], unique=False)
        batch_op.create_index(batch_op.f("ix_event_checkins_event_date"), ["event_date"], unique=False)
        batch_op.create_unique_constraint("uq_event_checkins_event_date_cpf", ["event_date", "cpf"])

    # ---------------------- MATRICULAS ----------------------
    with op.batch_alter_table("matriculas", schema=None) as batch_op:
        # INTEGER -> BIGINT
        batch_op.alter_column(
            "id",
            existing_type=sa.INTEGER(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )

        # code VARCHAR(16) -> String(8)
        batch_op.alter_column(
            "code",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.String(length=8),
            existing_nullable=False,
        )

        # cpf obrigatório
        batch_op.alter_column(
            "cpf",
            existing_type=sa.VARCHAR(length=11),
            nullable=False,
        )

        # birth_date DATE -> String(10)
        batch_op.alter_column(
            "birth_date",
            existing_type=sa.DATE(),
            type_=sa.String(length=10),
            existing_nullable=True,
        )

        # status pode ser nulo (ou mantenha NOT NULL + server_default se preferir)
        batch_op.alter_column(
            "status",
            existing_type=sa.VARCHAR(length=20),
            nullable=True,
            existing_server_default=sa.text("'active'::character varying"),
        )

        # created_at TIMESTAMP -> TIMESTAMPTZ
        batch_op.alter_column(
            "created_at",
            existing_type=postgresql.TIMESTAMP(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
            existing_server_default=sa.text("CURRENT_TIMESTAMP"),
        )

        # reindex
        batch_op.drop_index("ix_matriculas_matriculas_code")
        batch_op.drop_index("ix_matriculas_matriculas_cpf")
        batch_op.create_index(batch_op.f("ix_matriculas_code"), ["code"], unique=True)
        batch_op.create_index(batch_op.f("ix_matriculas_cpf"), ["cpf"], unique=False)

    # ---------------------- PRESENCAS ----------------------
    with op.batch_alter_table("presencas", schema=None) as batch_op:
        # INTEGER -> BIGINT
        batch_op.alter_column(
            "id",
            existing_type=sa.INTEGER(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )

        # FK INTEGER -> BIGINT
        batch_op.alter_column(
            "matricula_id",
            existing_type=sa.INTEGER(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )

        # date_key VARCHAR(10) -> DATE (com USING)
        batch_op.alter_column(
            "date_key",
            existing_type=sa.VARCHAR(length=10),
            type_=sa.Date(),
            existing_nullable=False,
            postgresql_using="date_key::date",
        )

        # timestamp TIMESTAMP -> TIMESTAMPTZ (assumindo que os valores antigos estavam em UTC "naive")
        batch_op.alter_column(
            "timestamp",
            existing_type=postgresql.TIMESTAMP(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
            postgresql_using="timezone('UTC', timestamp)",
            existing_server_default=sa.text("CURRENT_TIMESTAMP"),
        )

        # source VARCHAR(80) -> String(20)
        batch_op.alter_column(
            "source",
            existing_type=sa.VARCHAR(length=80),
            type_=sa.String(length=20),
            existing_nullable=True,
        )

        # reindex / unique por dia removido e refeito depois se quiser
        batch_op.drop_index("ix_presencas_presencas_date_key")
        batch_op.drop_index("ix_presencas_presencas_matricula_id")
        batch_op.drop_constraint("uq_presenca_por_dia", type_="unique")
        batch_op.create_index(batch_op.f("ix_presencas_date_key"), ["date_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_presencas_matricula_id"), ["matricula_id"], unique=False)


def downgrade():
    # ---------------------- PRESENCAS ----------------------
    with op.batch_alter_table("presencas", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_presencas_matricula_id"))
        batch_op.drop_index(batch_op.f("ix_presencas_date_key"))
        batch_op.create_unique_constraint(
            "uq_presenca_por_dia",
            ["matricula_id", "date_key"],
            postgresql_nulls_not_distinct=False,
        )
        batch_op.create_index("ix_presencas_presencas_matricula_id", ["matricula_id"], unique=False)
        batch_op.create_index("ix_presencas_presencas_date_key", ["date_key"], unique=False)

        batch_op.alter_column(
            "source",
            existing_type=sa.String(length=20),
            type_=sa.VARCHAR(length=80),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "timestamp",
            existing_type=sa.DateTime(timezone=True),
            type_=postgresql.TIMESTAMP(),
            existing_nullable=False,
            existing_server_default=sa.text("CURRENT_TIMESTAMP"),
        )
        batch_op.alter_column(
            "date_key",
            existing_type=sa.Date(),
            type_=sa.VARCHAR(length=10),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "matricula_id",
            existing_type=sa.BigInteger(),
            type_=sa.INTEGER(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "id",
            existing_type=sa.BigInteger(),
            type_=sa.INTEGER(),
            existing_nullable=False,
        )

    # ---------------------- MATRICULAS ----------------------
    with op.batch_alter_table("matriculas", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_matriculas_cpf"))
        batch_op.drop_index(batch_op.f("ix_matriculas_code"))
        batch_op.create_index("ix_matriculas_matriculas_cpf", ["cpf"], unique=False)
        batch_op.create_index("ix_matriculas_matriculas_code", ["code"], unique=True)

        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            type_=postgresql.TIMESTAMP(),
            existing_nullable=False,
            existing_server_default=sa.text("CURRENT_TIMESTAMP"),
        )
        batch_op.alter_column(
            "status",
            existing_type=sa.VARCHAR(length=20),
            nullable=False,
            existing_server_default=sa.text("'active'::character varying"),
        )
        batch_op.alter_column(
            "birth_date",
            existing_type=sa.String(length=10),
            type_=sa.DATE(),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "cpf",
            existing_type=sa.VARCHAR(length=11),
            nullable=True,
        )
        batch_op.alter_column(
            "code",
            existing_type=sa.String(length=8),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "id",
            existing_type=sa.BigInteger(),
            type_=sa.INTEGER(),
            existing_nullable=False,
        )

    # ---------------------- EVENT_CHECKINS ----------------------
    with op.batch_alter_table("event_checkins", schema=None) as batch_op:
        batch_op.drop_constraint("uq_event_checkins_event_date_cpf", type_="unique")
        batch_op.drop_index(batch_op.f("ix_event_checkins_event_date"))
        batch_op.drop_index(batch_op.f("ix_event_checkins_cpf"))
        batch_op.create_unique_constraint(
            "uq_event_date_cpf", ["event_date", "cpf"], postgresql_nulls_not_distinct=False
        )
        batch_op.create_index("ix_event_checkins_event_checkins_event_date", ["event_date"], unique=False)
        batch_op.create_index("ix_event_checkins_event_checkins_cpf", ["cpf"], unique=False)

        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            type_=postgresql.TIMESTAMP(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            type_=postgresql.TIMESTAMP(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "birth_date",
            existing_type=sa.VARCHAR(length=10),
            nullable=False,
        )
        batch_op.alter_column(
            "id",
            existing_type=sa.BigInteger(),
            type_=sa.INTEGER(),
            existing_nullable=False,
        )
        batch_op.drop_column("name")

    # ---------------------- USERS ----------------------
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index("uq_users_username_lower", postgresql_using="btree")
    op.drop_table("users")