from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "36d1369e84c4"
down_revision = None
branch_labels = None
depends_on = None

def _has_table(bind, name: str) -> bool:
    insp = sa.inspect(bind)
    return insp.has_table(name)

def _has_index(bind, table: str, index_name: str) -> bool:
    insp = sa.inspect(bind)
    return any(ix.get("name") == index_name for ix in insp.get_indexes(table))

def upgrade():
    bind = op.get_bind()

    # ----------------- matriculas -----------------
    if not _has_table(bind, "matriculas"):
        op.create_table(
            "matriculas",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=16), nullable=False),
            sa.Column("holder_name", sa.String(length=120), nullable=True),
            sa.Column("cpf", sa.String(length=11), nullable=True),   # sem UNIQUE por enquanto
            sa.Column("birth_date", sa.String(length=10), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default=text("'active'")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id", name="pk_matriculas"),
            sa.UniqueConstraint("code", name="uq_matriculas_code"),
        )
    # índices (cria só se faltar)
    if _has_table(bind, "matriculas") and not _has_index(bind, "matriculas", "ix_matriculas_code"):
        op.create_index("ix_matriculas_code", "matriculas", ["code"])
    if _has_table(bind, "matriculas") and not _has_index(bind, "matriculas", "ix_matriculas_cpf"):
        op.create_index("ix_matriculas_cpf", "matriculas", ["cpf"])

    # ----------------- presencas -----------------
    if not _has_table(bind, "presencas"):
        op.create_table(
            "presencas",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("matricula_id", sa.Integer(), nullable=False),
            sa.Column("date_key", sa.String(length=10), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
            sa.Column("ip", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=300), nullable=True),
            sa.Column("source", sa.String(length=80), nullable=True),
            sa.PrimaryKeyConstraint("id", name="pk_presencas"),
            sa.UniqueConstraint("matricula_id", "date_key", name="uq_presenca_por_dia"),
            sa.ForeignKeyConstraint(
                ["matricula_id"], ["matriculas.id"],
                name="fk_presencas_matricula_id_matriculas",
            ),
        )
    if _has_table(bind, "presencas") and not _has_index(bind, "presencas", "ix_presencas_matricula_id"):
        op.create_index("ix_presencas_matricula_id", "presencas", ["matricula_id"])
    if _has_table(bind, "presencas") and not _has_index(bind, "presencas", "ix_presencas_date_key"):
        op.create_index("ix_presencas_date_key", "presencas", ["date_key"])

def downgrade():
    # Downgrade "best-effort": apaga índices se existirem e depois as tabelas
    bind = op.get_bind()

    if _has_table(bind, "presencas"):
        if _has_index(bind, "presencas", "ix_presencas_date_key"):
            op.drop_index("ix_presencas_date_key", table_name="presencas")
        if _has_index(bind, "presencas", "ix_presencas_matricula_id"):
            op.drop_index("ix_presencas_matricula_id", table_name="presencas")
        op.drop_table("presencas")

    if _has_table(bind, "matriculas"):
        if _has_index(bind, "matriculas", "ix_matriculas_cpf"):
            op.drop_index("ix_matriculas_cpf", table_name="matriculas")
        if _has_index(bind, "matriculas", "ix_matriculas_code"):
            op.drop_index("ix_matriculas_code", table_name="matriculas")
        op.drop_table("matriculas")
