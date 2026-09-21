"""
Medicine reminder scheduler — uses APScheduler to send email reminders
at scheduled medicine dosage times.
"""

from datetime import datetime, time
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.logging import get_logger
from app.models.prescription import Prescription
from app.services.email_service import EmailService

logger = get_logger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler():
    """Start the background scheduler if not already running."""
    if not scheduler.running:
        scheduler.start()
        logger.info("reminder_scheduler_started")


def shutdown_scheduler():
    """Shutdown scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("reminder_scheduler_stopped")


def schedule_prescription_reminders(
    prescription: Prescription,
    patient_email: str,
    patient_name: str,
    doctor_name: str,
):
    """
    Schedule daily medicine reminder jobs for each configured slot in the prescription.
    """
    if not scheduler.running:
        try:
            start_scheduler()
        except Exception as e:
            logger.warning(f"Could not start scheduler inline: {e}")

    slots = [
        ("Morning", "morning", "morning_time", "morning_before_meal"),
        ("Afternoon", "afternoon", "afternoon_time", "afternoon_before_meal"),
        ("Evening", "evening", "evening_time", "evening_before_meal"),
        ("Night", "night", "night_time", "night_before_meal"),
    ]

    for med in prescription.medicines:
        for slot_label, enabled_attr, time_attr, meal_attr in slots:
            is_enabled = getattr(med, enabled_attr, False)
            slot_time: Optional[time] = getattr(med, time_attr, None)
            before_meal: bool = getattr(med, meal_attr, True)

            if is_enabled and slot_time:
                job_id = f"med_remind_{med.id}_{slot_label.lower()}"
                time_str = slot_time.strftime("%I:%M %p")

                start_dt = datetime.combine(med.start_date, slot_time)
                end_dt = datetime.combine(med.end_date, time(23, 59, 59))

                try:
                    scheduler.add_job(
                        EmailService.send_medicine_reminder_email,
                        trigger=CronTrigger(
                            hour=slot_time.hour,
                            minute=slot_time.minute,
                            start_date=start_dt,
                            end_date=end_dt,
                        ),
                        id=job_id,
                        name=f"Reminder for {med.medicine_name} ({slot_label})",
                        args=[
                            patient_email,
                            patient_name,
                            doctor_name,
                            med.medicine_name,
                            slot_label,
                            time_str,
                            before_meal,
                        ],
                        replace_existing=True,
                    )
                    logger.info(
                        f"scheduled_medicine_reminder: job_id={job_id} time={time_str} start={med.start_date} end={med.end_date}"
                    )
                except Exception as e:
                    logger.error(f"failed_to_schedule_reminder: job_id={job_id} error={str(e)}")


def schedule_plan_reminders(
    plan: Any,
    patient_email: str,
    patient_name: str,
):
    """
    Schedule daily recurring reminder jobs for each active item in an approved wellness plan.
    """
    if not scheduler.running:
        try:
            start_scheduler()
        except Exception as e:
            logger.warning(f"Could not start scheduler inline: {e}")

    for item in plan.items:
        if not item.is_active:
            continue

        try:
            time_parts = item.time_of_day.split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1])
        except (ValueError, IndexError):
            logger.warning(f"Invalid time format on plan item {item.id}: {item.time_of_day}")
            continue

        job_id = f"plan_remind_{plan.id}_{item.id}"

        try:
            scheduler.add_job(
                EmailService.send_plan_reminder_email,
                trigger=CronTrigger(hour=hour, minute=minute),
                id=job_id,
                name=f"Plan Reminder: {item.title} ({item.time_of_day})",
                args=[
                    patient_email,
                    patient_name,
                    plan.title,
                    item.title,
                    item.time_of_day,
                    item.category,
                    item.description,
                ],
                replace_existing=True,
            )
            logger.info(
                f"scheduled_plan_reminder: job_id={job_id} time={item.time_of_day} title={item.title}"
            )
        except Exception as e:
            logger.error(f"failed_to_schedule_plan_reminder: job_id={job_id} error={str(e)}")


def cancel_plan_reminders(plan: Any):
    """
    Cancel all scheduled reminder jobs for a paused, completed, or cancelled plan.
    """
    if not scheduler.running:
        return

    for item in plan.items:
        job_id = f"plan_remind_{plan.id}_{item.id}"
        try:
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
                logger.info(f"cancelled_plan_reminder: job_id={job_id}")
        except Exception as e:
            logger.warning(f"Error removing reminder job {job_id}: {e}")

