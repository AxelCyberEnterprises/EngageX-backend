# Generated by Django 5.1.7 on 2025-04-05 13:34

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('practice_sessions', '0003_remove_sessionchunk_end_time_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sessionchunk',
            name='video_file',
            field=models.CharField(blank=True, help_text='Video file for this chunk', max_length=255, null=True),
        ),
    ]
