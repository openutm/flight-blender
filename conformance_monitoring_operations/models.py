import json
import uuid
from datetime import datetime

from django.db import models

# Source: https://stackoverflow.com/questions/10194975/how-to-dynamically-add-remove-periodic-tasks-to-celery-celerybeat
# Create your models here.
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from common.data_definitions import CONFORMANCE_STATES
from flight_declaration_operations.models import FlightDeclaration
from geo_fence_operations.models import GeoFence


class ConformanceRecord(models.Model):
    id = models.UUIDField(
        default=uuid.uuid4,
        primary_key=True,
        editable=False,
        help_text="Unique identifier for the conformance record",
    )
    flight_declaration = models.ForeignKey(
        FlightDeclaration,
        on_delete=models.CASCADE,
        help_text="The flight declaration associated with this conformance record",
    )
    conformance_state = models.IntegerField(
        choices=CONFORMANCE_STATES,
        help_text="The conformance state of the flight declaration at the time of the record",
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the conformance record was created",
    )
    description = models.TextField(
        help_text="Description of the conformance event (deviation or rectification)",
    )
    event_type = models.CharField(
        max_length=20,
        choices=[("deviation", "Deviation"), ("rectification", "Rectification")],
        help_text="Type of conformance event: deviation or rectification",
    )
    geofence_breach = models.BooleanField(
        default=False,
        help_text="Indicates whether the event involved a geofence breach",
    )
    geofence = models.ForeignKey(
        GeoFence,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text="The geofence involved in the event, if applicable",
    )
    resolved = models.BooleanField(
        default=False,
        help_text="Indicates whether the conformance issue has been resolved",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the conformance record was created",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when the conformance record was last updated",
    )

    def get_conformance_state_display_text(self):
        """Returns the human-readable text for the conformance state"""
        return dict(CONFORMANCE_STATES).get(self.conformance_state, "Unknown")

    def __str__(self):
        return f"Conformance Record {self.id} for Flight Declaration {self.flight_declaration.id}"


class TaskScheduler(models.Model):
    periodic_task = models.ForeignKey(PeriodicTask, on_delete=models.CASCADE)
    flight_declaration = models.OneToOneField(
        FlightDeclaration,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        help_text="The flight declaration that this task is associated with this task scheduler",
    )
    session_id = models.UUIDField(
        blank=True,
        null=True,
        help_text="The session id that this task is associated with this task scheduler",
    )

    @staticmethod
    def schedule_every(
        task_name: str,
        period,
        every: int,
        session_id: str,
        expires: str,
        flight_declaration: FlightDeclaration | None,
        args=None,
        kwargs=None,
    ):
        """schedules a task by name every "every" "period". So an example call would be:
        TaskScheduler('mycustomtask', 'seconds', 30, [1,2,3])
        that would schedule your custom task to run every 30 seconds with the arguments 1,2 and 3 passed to the actual task.
        """
        flight_declaration_id = str(flight_declaration.id) if flight_declaration else "00000000-0000-0000-0000-000000000000"
        permissible_periods = ["days", "hours", "minutes", "seconds"]
        if period not in permissible_periods:
            raise Exception("Invalid period specified")
        # create the periodic task and the interval
        ptask_name = "{}_{}".format(
            task_name,
            datetime.now(),
        )  # create some name for the period task
        interval_schedules = IntervalSchedule.objects.filter(period=period, every=every)
        if interval_schedules:  # just check if interval schedules exist like that already and reuse em
            interval_schedule = interval_schedules[0]
        else:  # create a brand new interval schedule
            interval_schedule = IntervalSchedule()
            interval_schedule.every = every  # should check to make sure this is a positive int
            interval_schedule.period = period
            interval_schedule.save()
        ptask = PeriodicTask(
            name=ptask_name,
            task=task_name,
            interval=interval_schedule,
            kwargs=json.dumps(
                {
                    "flight_declaration_id": flight_declaration_id,
                    "session_id": session_id,
                }
            ),
            expires=expires,
        )
        if args:
            ptask.args = args
        if kwargs:
            ptask.kwargs = kwargs
        ptask.save()
        created_task = TaskScheduler.objects.create(
            periodic_task=ptask,
            session_id=session_id,
            flight_declaration=flight_declaration,
        )

        return created_task

    def stop(self):
        """pauses the task"""
        ptask = self.periodic_task
        ptask.enabled = False
        ptask.save()

    def start(self):
        """starts the task"""
        ptask = self.periodic_task
        ptask.enabled = True
        ptask.save()

    def terminate(self):
        self.stop()
        ptask = self.periodic_task
        self.delete()
        ptask.delete()
