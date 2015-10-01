# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


def mark_published_versions(apps, schema_editor):
    """
    Mark Pipelines that are published accordingly.

    Before this migration, Pipelines were published via the ForeignKey on PipelineFamily.
    Now, we mark it using a BooleanField on Pipeline.
    """
    PipelineFamily = apps.get_model("pipeline", "PipelineFamily")
    for pf in PipelineFamily.objects.all():
        published_version = pf.published_version
        if published_version is not None:
            published_version.published = True
            published_version.save()


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0012_pipeline_published'),
    ]

    operations = [
        migrations.RunPython(mark_published_versions)
    ]