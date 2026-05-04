from fastapi import APIRouter, Depends

from app.auth import AuthedUser, require_auth

router = APIRouter()


@router.get("/me")
async def get_me(user: AuthedUser = Depends(require_auth)) -> dict[str, object]:
    return {
        "userId": user.userId,
        "email": user.email,
        "firstName": user.firstName,
        "lastName": user.lastName,
        "imageUrl": user.imageUrl,
        "role": user.role,
    }
