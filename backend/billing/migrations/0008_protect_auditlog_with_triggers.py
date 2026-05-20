from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0007_jobrun"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION billing_prevent_auditlog_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'billing_auditlog is append-only and cannot be updated or deleted';
            END;
            $$ LANGUAGE plpgsql;

            DROP TRIGGER IF EXISTS billing_auditlog_prevent_update ON billing_auditlog;
            CREATE TRIGGER billing_auditlog_prevent_update
            BEFORE UPDATE ON billing_auditlog
            FOR EACH ROW EXECUTE FUNCTION billing_prevent_auditlog_mutation();

            DROP TRIGGER IF EXISTS billing_auditlog_prevent_delete ON billing_auditlog;
            CREATE TRIGGER billing_auditlog_prevent_delete
            BEFORE DELETE ON billing_auditlog
            FOR EACH ROW EXECUTE FUNCTION billing_prevent_auditlog_mutation();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS billing_auditlog_prevent_update ON billing_auditlog;
            DROP TRIGGER IF EXISTS billing_auditlog_prevent_delete ON billing_auditlog;
            DROP FUNCTION IF EXISTS billing_prevent_auditlog_mutation();
            """,
        ),
    ]
