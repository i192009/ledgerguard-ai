"""Tenant isolation for anomaly artifacts created before the scope column existed."""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("anomaly_model_versions")}
    if "tenant_id" not in columns:
        op.add_column(
            "anomaly_model_versions",
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="tenant-demo"),
        )
        op.create_foreign_key(
            "fk_anomaly_model_tenant", "anomaly_model_versions", "tenants", ["tenant_id"], ["id"]
        )
        op.create_index("ix_anomaly_model_versions_tenant_id", "anomaly_model_versions", ["tenant_id"])


def downgrade():
    # Tenant scope is intentionally retained on downgrade to avoid weakening isolation.
    pass
