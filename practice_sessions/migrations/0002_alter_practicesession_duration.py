# Generated by Django 5.1.7 on 2025-04-01 19:20

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('practice_sessions', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='practicesession',
            name='duration',
            field=models.DurationField(blank=True, help_text='Duration of the session', null=True),
        ),
    ]
