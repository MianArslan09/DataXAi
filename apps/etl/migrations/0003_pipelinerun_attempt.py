"""Add optional run lineage. Reversal drops links but preserves runs and attempts.

Reapplying leaves existing links NULL. Preserve the mapping separately before
an intentional rollback if those associations must be restored.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("etl", "0002_staging_schema")]

    operations = [
        migrations.AddField(
            model_name="pipelinerun",
            name="attempt",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.PROTECT,
                related_name="pipeline_run",
                to="etl.transformationattempt",
            ),
        ),
    ]
