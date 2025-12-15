from pydantic import BaseModel, EmailStr, Field


class LoginForm(BaseModel):
    email: EmailStr
    password: str = Field(min_length=4)


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=4)
    role: str = "user"
    is_active: bool = True
