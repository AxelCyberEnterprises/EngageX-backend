# Generated by Django 5.1.6 on 2025-03-21 04:52

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0003_alter_userprofile_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='purpose',
            field=models.CharField(choices=[('admin', 'Admin'), ('user', 'User'), ('coach', 'Coach')], default='user',
                                   max_length=20),
        ),
    ]
