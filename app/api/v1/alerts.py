from fastapi import APIRouter, BackgroundTasks, Response, status

from app.api.deps import DbSession, SessionFactoryDep
from app.schemas import DiscordAlertCreate, DiscordAlertCreated, ErrorResponse
from app.services import notifications

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post(
    "/discord",
    response_model=DiscordAlertCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Send suspension alerts to a Discord channel",
    description=(
        "Give a Discord channel webhook URL and, optionally, a place to follow. Every time a "
        "suspension there is verified, the channel gets a message with the details and the proof. "
        "Save the returned `manage_token`: it is the only way to turn the alert off."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Unknown location"},
        409: {"model": ErrorResponse, "description": "Already subscribed"},
    },
)
def create_discord_alert(
    payload: DiscordAlertCreate,
    db: DbSession,
    background: BackgroundTasks,
    session_factory: SessionFactoryDep,
):
    sub = notifications.create_discord_alert(db, payload)
    if payload.send_test:
        background.add_task(
            notifications.send_all, [notifications.welcome_delivery(sub)], session_factory
        )
    return DiscordAlertCreated.model_validate(sub)


@router.delete(
    "/{manage_token}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Turn an alert off",
    responses={404: {"model": ErrorResponse}},
)
def delete_alert(manage_token: str, db: DbSession) -> Response:
    notifications.deactivate_alert(db, manage_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
