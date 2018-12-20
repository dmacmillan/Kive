# -*- coding: utf-8 -*-
# Generated by Django 1.11.15 on 2018-11-26 23:25
from __future__ import unicode_literals

from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('librarian', '0106_dataset_name_not_blank'),
        ('auth', '0008_alter_user_username_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('container', '0004_containerapp_containerargument'),
    ]

    operations = [
        migrations.CreateModel(
            name='Batch',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=60, verbose_name=b'Name of this batch of container runs')),
                ('description', models.TextField(blank=True, max_length=1000)),
                ('groups_allowed', models.ManyToManyField(blank=True, help_text='What groups have access?', related_name='container_batch_has_access_to', to='auth.Group')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('users_allowed', models.ManyToManyField(blank=True, help_text='Which users have access?', related_name='container_batch_has_access_to', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ContainerDataset',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Local file name, also used to sort multiple inputs for a single argument.', max_length=60)),
                ('created', models.DateTimeField(auto_now_add=True, help_text='When this was added to Kive.')),
                ('argument', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='datasets', to='container.ContainerArgument')),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='containers', to='librarian.Dataset')),
            ],
        ),
        migrations.CreateModel(
            name='ContainerLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(choices=[(b'O', b'stdout'), (b'E', b'stderr')], max_length=1)),
                ('short_text', models.CharField(blank=True, help_text="Holds the log text if it's shorter than the max length.", max_length=2000)),
                ('long_text', models.FileField(help_text="Holds the log text if it's longer than the max length.", upload_to=b'')),
            ],
        ),
        migrations.CreateModel(
            name='ContainerRun',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_time', models.DateTimeField(blank=True, help_text='Starting time', null=True, verbose_name='start time')),
                ('end_time', models.DateTimeField(blank=True, help_text='Ending time', null=True, verbose_name='end time')),
                ('name', models.CharField(blank=True, max_length=60)),
                ('description', models.CharField(blank=True, max_length=1000)),
                ('state', models.CharField(choices=[(b'N', b'New'), (b'P', b'Pending'), (b'R', b'Running'), (b'S', b'Saving'), (b'C', b'Complete'), (b'F', b'Failed'), (b'X', b'Cancelled')], default=b'N', max_length=1)),
                ('sandbox_path', models.CharField(blank=True, max_length=4096)),
                ('return_code', models.IntegerField(blank=True, null=True)),
                ('is_redacted', models.BooleanField(default=False, help_text='True if the outputs or logs were redacted for sensitive data')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='containerapp',
            name='memory',
            field=models.PositiveIntegerField(default=6000, help_text='Megabytes of memory Slurm will allocate for this app (0 allocates all memory)', verbose_name=b'Memory required (MB)'),
        ),
        migrations.AddField(
            model_name='containerapp',
            name='threads',
            field=models.PositiveIntegerField(default=1, help_text='How many threads does this app use during execution?', validators=[django.core.validators.MinValueValidator(1)], verbose_name=b'Number of threads'),
        ),
        migrations.AddField(
            model_name='containerrun',
            name='app',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='runs', to='container.ContainerApp'),
        ),
        migrations.AddField(
            model_name='containerrun',
            name='batch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='runs', to='container.Batch'),
        ),
        migrations.AddField(
            model_name='containerrun',
            name='groups_allowed',
            field=models.ManyToManyField(blank=True, help_text='What groups have access?', related_name='container_containerrun_has_access_to', to='auth.Group'),
        ),
        migrations.AddField(
            model_name='containerrun',
            name='stopped_by',
            field=models.ForeignKey(blank=True, help_text='User that stopped this run', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='container_runs_stopped', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='containerrun',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='containerrun',
            name='users_allowed',
            field=models.ManyToManyField(blank=True, help_text='Which users have access?', related_name='container_containerrun_has_access_to', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='containerlog',
            name='run',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logs', to='container.ContainerRun'),
        ),
        migrations.AddField(
            model_name='containerdataset',
            name='run',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='datasets', to='container.ContainerRun'),
        ),
    ]
