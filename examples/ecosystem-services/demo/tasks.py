"""Celery jobs receive identifiers, not request-scoped model instances."""

from project.celery import app

from .models import Job


@app.task(name="example.complete_job")
def complete_job(job_id: int) -> None:
    Job.objects.filter(pk=job_id).update(completed=True)
