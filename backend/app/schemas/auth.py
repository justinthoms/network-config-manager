from pydantic import BaseModel, Field

class LoginRequest(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
