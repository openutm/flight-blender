# Generated by Django 4.2.4 on 2023-09-02 09:37

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("flight_declaration_operations", "0006_flightoperationtracking"),
    ]

    operations = [
        migrations.AlterField(
            model_name="flightdeclaration",
            name="state",
            field=models.IntegerField(
                choices=[
                    (0, "Not Submitted"),
                    (1, "Accepted"),
                    (2, "Activated"),
                    (3, "Nonconforming"),
                    (4, "Contingent"),
                    (5, "Ended"),
                    (6, "Withdrawn"),
                    (7, "Cancelled"),
                    (8, "Rejected"),
                ],
                default=0,
                help_text="Set the state of operation",
            ),
        ),
    ]
