# Generated by Django 5.1.8 on 2025-05-23 13:55

import datetime
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('constraint_operations', '0005_constraintreference_flight_declaration'),
        ('flight_declaration_operations', '0015_alter_flightoperationalintentreference_ovn_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompositeConstraint',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('bounds', models.CharField(max_length=140)),
                ('start_datetime', models.DateTimeField(default=datetime.datetime.now)),
                ('end_datetime', models.DateTimeField(default=datetime.datetime.now)),
                ('alt_max', models.FloatField()),
                ('alt_min', models.FloatField()),
                ('constraint_detail', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='composite_constraint_detail', to='constraint_operations.constraintdetail')),
                ('constraint_reference', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='composite_constraint_reference', to='constraint_operations.constraintreference')),
                ('declaration', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='flight_declaration_operations.flightdeclaration')),
            ],
        ),
    ]
