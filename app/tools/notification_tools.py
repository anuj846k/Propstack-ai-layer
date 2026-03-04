import asyncio

from app.dependencies import get_supabase


def _insert_notification(
    user_id: str, title: str, message: str, notification_type: str
) -> dict:
    sb = get_supabase()
    result = (
        sb.table("notifications")
        .insert({
            "user_id": user_id,
            "title": title,
            "message": message,
            "type": notification_type,
        })
        .execute()
    )
    return {
        "status": "success",
        "message": "Notification created",
        "data": {"notification_id": result.data[0]["id"]},
        "error_message": None,
    }


async def create_notification(
    user_id: str,
    title: str,
    message: str,
    notification_type: str,
) -> dict:
    """Creates a notification for a user (landlord or tenant) in the system.

    Used to inform the landlord about call results or payment commitments,
    or to notify tenants about upcoming due dates.

    Args:
        user_id (str): UUID of the user to notify.
        title (str): Short notification title e.g. 'Rent Call Completed'.
        message (str): Detailed notification body.
        notification_type (str): One of 'rent_due', 'ticket_created',
                          'ticket_assigned', 'status_changed', 'lease_expiring'.

    Returns:
        dict: status ('success' or 'error') and notification_id if created.
    """
    try:
        return await asyncio.to_thread(
            _insert_notification, user_id, title, message, notification_type
        )
    except Exception as e:
        return {
            "status": "error",
            "message": "Failed to create notification",
            "data": None,
            "error_message": str(e),
        }
