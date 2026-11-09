from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel

from open_webui.models.users import Users, UserModel
from open_webui.models.feedbacks import (
    FeedbackModel,
    FeedbackResponse,
    FeedbackForm,
    Feedbacks,
)

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.evaluation_compat import (
    id_projection,
    leaderboard as leaderboard_projection,
    list_projection,
    model_history,
    model_ids,
)

router = APIRouter()


############################
# GetConfig
############################


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_EVALUATION_ARENA_MODELS": request.app.state.config.ENABLE_EVALUATION_ARENA_MODELS,
        "EVALUATION_ARENA_MODELS": request.app.state.config.EVALUATION_ARENA_MODELS,
    }


############################
# UpdateConfig
############################


class UpdateConfigForm(BaseModel):
    ENABLE_EVALUATION_ARENA_MODELS: Optional[bool] = None
    EVALUATION_ARENA_MODELS: Optional[list[dict]] = None


@router.post("/config")
async def update_config(
    request: Request,
    form_data: UpdateConfigForm,
    user=Depends(get_admin_user),
):
    config = request.app.state.config
    if form_data.ENABLE_EVALUATION_ARENA_MODELS is not None:
        config.ENABLE_EVALUATION_ARENA_MODELS = form_data.ENABLE_EVALUATION_ARENA_MODELS
    if form_data.EVALUATION_ARENA_MODELS is not None:
        config.EVALUATION_ARENA_MODELS = form_data.EVALUATION_ARENA_MODELS
    return {
        "ENABLE_EVALUATION_ARENA_MODELS": config.ENABLE_EVALUATION_ARENA_MODELS,
        "EVALUATION_ARENA_MODELS": config.EVALUATION_ARENA_MODELS,
    }


class FeedbackUserReponse(BaseModel):
    id: str
    name: str
    email: str
    role: str = "pending"

    last_active_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch


class FeedbackUserResponse(FeedbackResponse):
    user: Optional[FeedbackUserReponse] = None


class FeedbackIdResponse(BaseModel):
    id: str
    user_id: str
    created_at: int
    updated_at: int


class FeedbackListResponse(BaseModel):
    items: list[FeedbackUserResponse]
    total: int


class LeaderboardEntry(BaseModel):
    model_id: str
    rating: int
    won: int
    lost: int
    count: int
    top_tags: list[dict]


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]


class ModelHistoryEntry(BaseModel):
    date: str
    won: int
    lost: int


class ModelHistoryResponse(BaseModel):
    model_id: str
    history: list[ModelHistoryEntry]


@router.get("/feedbacks/all", response_model=list[FeedbackUserResponse])
async def get_all_feedbacks(user=Depends(get_admin_user)):
    feedbacks = Feedbacks.get_all_feedbacks()
    return [
        FeedbackUserResponse(
            **feedback.model_dump(),
            user=FeedbackUserReponse(
                **Users.get_user_by_id(feedback.user_id).model_dump()
            ),
        )
        for feedback in feedbacks
    ]


@router.get("/feedbacks/all/ids", response_model=list[FeedbackIdResponse])
async def get_all_feedback_ids(user=Depends(get_admin_user)):
    return id_projection(Feedbacks.get_all_feedbacks())


@router.get("/feedbacks/list", response_model=FeedbackListResponse)
async def get_feedback_list(
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = 1,
    model_id: Optional[str] = None,
    user=Depends(get_admin_user),
):
    return list_projection(
        Feedbacks.get_all_feedbacks(),
        Users.get_user_by_id,
        order_by=order_by,
        direction=direction,
        page=page,
        model_id=model_id,
    )


@router.get("/feedbacks/models", response_model=list[str])
async def get_feedback_models(user=Depends(get_admin_user)):
    return model_ids(Feedbacks.get_all_feedbacks())


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(query: Optional[str] = None, user=Depends(get_admin_user)):
    return leaderboard_projection(Feedbacks.get_all_feedbacks())


@router.get("/leaderboard/{model_id}/history", response_model=ModelHistoryResponse)
async def get_model_history(model_id: str, days: int = 30, user=Depends(get_admin_user)):
    return model_history(Feedbacks.get_all_feedbacks(), model_id, days)


@router.delete("/feedbacks/all")
async def delete_all_feedbacks(user=Depends(get_admin_user)):
    success = Feedbacks.delete_all_feedbacks()
    return success


@router.get("/feedbacks/all/export", response_model=list[FeedbackModel])
async def get_all_feedbacks(user=Depends(get_admin_user)):
    feedbacks = Feedbacks.get_all_feedbacks()
    return [
        FeedbackModel(
            **feedback.model_dump(), user=Users.get_user_by_id(feedback.user_id)
        )
        for feedback in feedbacks
    ]


@router.get("/feedbacks/user", response_model=list[FeedbackUserResponse])
async def get_feedbacks(user=Depends(get_verified_user)):
    feedbacks = Feedbacks.get_feedbacks_by_user_id(user.id)
    return feedbacks


@router.delete("/feedbacks", response_model=bool)
async def delete_feedbacks(user=Depends(get_verified_user)):
    success = Feedbacks.delete_feedbacks_by_user_id(user.id)
    return success


@router.post("/feedback", response_model=FeedbackModel)
async def create_feedback(
    request: Request,
    form_data: FeedbackForm,
    user=Depends(get_verified_user),
):
    feedback = Feedbacks.insert_new_feedback(user_id=user.id, form_data=form_data)
    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

    return feedback


@router.get("/feedback/{id}", response_model=FeedbackModel)
async def get_feedback_by_id(id: str, user=Depends(get_verified_user)):
    feedback = Feedbacks.get_feedback_by_id_and_user_id(id=id, user_id=user.id)

    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return feedback


@router.post("/feedback/{id}", response_model=FeedbackModel)
async def update_feedback_by_id(
    id: str, form_data: FeedbackForm, user=Depends(get_verified_user)
):
    feedback = Feedbacks.update_feedback_by_id_and_user_id(
        id=id, user_id=user.id, form_data=form_data
    )

    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return feedback


@router.delete("/feedback/{id}")
async def delete_feedback_by_id(id: str, user=Depends(get_verified_user)):
    if user.role == "admin":
        success = Feedbacks.delete_feedback_by_id(id=id)
    else:
        success = Feedbacks.delete_feedback_by_id_and_user_id(id=id, user_id=user.id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return success
