# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-06-02 18:55
from __future__ import unicode_literals

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    replaces = [(b'datachecking', '0001_initial'), (b'datachecking', '0002_blankcell'), (b'datachecking', '0003_auto_20150204_1703'), (b'datachecking', '0004_redacted_20150417_1128'), (b'datachecking', '0005_conflicting_SD_workaround_20151118_1025'), (b'datachecking', '0006_transition_SD_to_dataset_20151117_1748'), (b'datachecking', '0007_transition_SD_fks_20151117_1759'), (b'datachecking', '0008_runsic_input_integrity_check_20160323_1550'), (b'datachecking', '0100_unlink_apps')]

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # ('archive', '0027_permissions_remove_null_20160203_1033'),
        # ('librarian', '0007_transition_SD_to_dataset_20151117_1748'),
        # ('librarian', '0001_initial'),
        # ('archive', '0002_auto_20150128_0950'),
        ('contenttypes', '0001_initial'),
        # ('metadata', '0101_squashed'),
    ]

    operations = [
        migrations.CreateModel(
            name='BadData',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('missing_output', models.BooleanField(default=False)),
                ('bad_header', models.NullBooleanField()),
                ('bad_num_rows', models.NullBooleanField()),
            ],
        ),
        migrations.CreateModel(
            name='CellError',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('row_num', models.PositiveIntegerField()),
                ('object_id', models.PositiveIntegerField(null=True)),
                ('baddata', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cell_errors', to='datachecking.BadData')),
                ('column', models.IntegerField(db_column='column_id')),
                ('content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='contenttypes.ContentType')),
            ],
        ),
        migrations.CreateModel(
            name='ContentCheckLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_time', models.DateTimeField(blank=True, help_text='Starting time', null=True, verbose_name='start time')),
                ('end_time', models.DateTimeField(blank=True, help_text='Ending time', null=True, verbose_name='end time')),
                ('execlog', models.IntegerField(db_column='execlog_id', null=True)),
                ('dataset', models.IntegerField(db_column='dataset_id')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='IntegrityCheckLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_time', models.DateTimeField(blank=True, help_text='Starting time', null=True, verbose_name='start time')),
                ('end_time', models.DateTimeField(blank=True, help_text='Ending time', null=True, verbose_name='end time')),
                ('execlog', models.IntegerField(db_column='execlog_id', null=True)),
                ('dataset', models.IntegerField(db_column='dataset_id')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='MD5Conflict',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('conflicting_dataset', models.IntegerField(db_column='conflicting_dataset_id', null=True)),
                ('integritychecklog', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='usurper', to='datachecking.IntegrityCheckLog')),
            ],
        ),
        migrations.CreateModel(
            name='VerificationLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_time', models.DateTimeField(blank=True, help_text='Starting time', null=True, verbose_name='start time')),
                ('end_time', models.DateTimeField(blank=True, help_text='Ending time', null=True, verbose_name='end time')),
                ('return_code', models.IntegerField(null=True)),
                ('output_log', models.FileField(upload_to='VerificationLogs')),
                ('error_log', models.FileField(upload_to='VerificationLogs')),
                ('CDTM', models.IntegerField(db_column='CDTM_id')),
                ('contentchecklog', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='verification_logs', to='datachecking.ContentCheckLog')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='baddata',
            name='contentchecklog',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='baddata', to='datachecking.ContentCheckLog'),
        ),
        migrations.CreateModel(
            name='BlankCell',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cellerror', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='blank', to='datachecking.CellError')),
            ],
        ),
        migrations.AddField(
            model_name='contentchecklog',
            name='user',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='integritychecklog',
            name='user',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='integritychecklog',
            name='read_failed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='integritychecklog',
            name='runsic',
            field=models.IntegerField(db_column='runsic_id', null=True),
        ),
    ]